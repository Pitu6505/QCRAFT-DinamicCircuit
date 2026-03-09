import json
import requests
from flask import request
import re

from sympy import Interval
from executeCircuitIBM import executeCircuitIBM
from executeCircuitAWS import runAWS, runAWS_save, code_to_circuit_aws
from ResettableTimer import ResettableTimer
from threading import Thread
from collections import deque
from DeepMochilaId_copy import optimizar_espacio_ml, SeleccionadorNN, ColaDataset, train_model
from dinamico_copy import optimizar_espacio_dinamico
from circuit_Compresor import AdvancedTopologyCompressor
from Ibm_api import get_backend_data as get_ibm_data
from Aws_api import get_aws_backend_data
import json
import numpy as np
import os
import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data
import threading
from typing import Callable, Iterator
import time
from qiskit_ibm_runtime import SamplerV2 as Sampler, QiskitRuntimeService
import qiskit.providers

MODEL_PATH = "modelo_entrenado.pth"
METADATA_PATH = "metadata.txt"


class Policy:
    """
    Class to store the queues and timers of a policy
    """
    def __init__(self, policy, max_qubits, time_limit_seconds, executeCircuit, aws_machine, ibm_machine):
        """
        Attributes:
            queues (dict): The queues of the policy
            timers (dict): The timers of the policy
        """
        self.queues = {'ibm': [], 'aws': []}
        self.timers = {'ibm': ResettableTimer(time_limit_seconds, lambda: policy(self.queues['ibm'], max_qubits, 'ibm', executeCircuit, ibm_machine)),
                       'aws': ResettableTimer(time_limit_seconds, lambda: policy(self.queues['aws'], max_qubits, 'aws', executeCircuit, aws_machine))}
        

class SchedulerPolicies:
    """
    Class to manage the policies of the scheduler

    Methods:
    --------
    service(service_name) 
        The request handler, adding the circuit to the selected queue
    
    executeCircuit(data,qb,shots,provider,urls)
        Executes the circuit in the selected provider
    
    most_repetitive(array)
        Returns the most repetitive element in an array
    
    create_circuit(urls,code,qb,provider)
        Creates the circuit to execute based on the URLs
    
    send_shots_optimized(queue, max_qubits, provider, executeCircuit, machine)
        Sends the URLs to the server with the minimum number of shots using the shots_optimized policy
    
    send_shots_depth(queue, max_qubits, provider, executeCircuit, machine)
        Sends the URLs to the server with the minimum number of shots and similar depth using the shots_depth policy
    
    send_depth(queue, max_qubits, provider, executeCircuit, machine)
        Sends the URLs to the server with the most similar depth using the depth policy
    
    send_shots(queue, max_qubits, provider, executeCircuit, machine)
        Sends the URLs to the server with the minimum number of shots using the shots policy
    
    send(queue, max_qubits, provider, executeCircuit, machine)
        Sends the URLs to the server using the time policy
    """
    def __init__(self, app):
        """
        Initializes the SchedulerPolicies class

        Attributes:
            app (Flask): The Flask app            
            time_limit_seconds (int): The time limit in seconds            
            max_qubits (int): The maximum number of qubits            
            machine_ibm (str): The IBM machine            
            machine_aws (str): The AWS machine            
            services (dict): The services of the scheduler            
            translator (str): The URL of the translator            
            unscheduler (str): The URL of the unscheduler
        """
        self.app = app
        self.time_limit_seconds = 10
        self.max_qubits = 156
        self.forced_threshold = 12
        self.machine_ibm =  'ibm_fez' #''local'
        self.machine_aws = 'local' #'arn:aws:braket:::device/quantum-simulator/amazon/sv1'
        self.executeCircuitIBM = executeCircuitIBM()
        
        # Inicializar compresores con topología física
        print("🔧 Inicializando compresores de topología...")
        
        # Compresor IBM
        try:
            # Intentar cargar topología desde el servicio ya inicializado
            if self.machine_ibm != 'local':
                service = self.executeCircuitIBM.service
                backend_ibm = service.backend(self.machine_ibm)
                cmap_ibm = backend_ibm.coupling_map
                self.compressor_ibm = AdvancedTopologyCompressor(coupling_map=cmap_ibm)
                print(f"✅ Compresor IBM inicializado con topología de {self.machine_ibm}")
            else:
                self.compressor_ibm = AdvancedTopologyCompressor(coupling_map=None)
                print(f"ℹ️  IBM en modo 'local' - compresor sin topología específica")
        except Exception as e:
            print(f"⚠️ No se pudo cargar topología IBM: {e}. Usando compresor sin topología.")
            self.compressor_ibm = AdvancedTopologyCompressor(coupling_map=None)
        
        # Compresor AWS
        try:
            if self.machine_aws != 'local':
                cmap_aws, backend_aws = get_aws_backend_data("Ankaa-3")
                self.compressor_aws = AdvancedTopologyCompressor(coupling_map=cmap_aws)
                print(f"✅ Compresor AWS inicializado con topología")
            else:
                self.compressor_aws = AdvancedTopologyCompressor(coupling_map=None)
                print(f"ℹ️  AWS en modo 'local' - compresor sin topología específica")
        except Exception as e:
            print(f"⚠️ No se pudo cargar topología AWS: {e}. Usando compresor sin topología.")
            self.compressor_aws = AdvancedTopologyCompressor(coupling_map=None)
        
        # Cargar modelo de ML si existe, sino entrenarlo
        self.model = SeleccionadorNN(input_dim=2, hidden_dim=16)

        if os.path.exists(MODEL_PATH):
            print("Cargando modelo entrenado...")
            self.model.load_state_dict(torch.load(MODEL_PATH))
            self.model.eval()
        else:
            print("Entrenando el modelo...")
            dataset = ColaDataset(num_samples=1000, max_items=20, capacidad=self.max_qubits, forced_threshold=self.forced_threshold)
            self.model = train_model(self.model, dataset, num_epochs=30, batch_size=32, learning_rate=0.001)
            torch.save(self.model.state_dict(), MODEL_PATH)


        self.services = {'time': Policy(self.send, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'shots': Policy(self.send_shots, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'depth': Policy(self.send_depth, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'shots_depth': Policy(self.send_shots_depth, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'shots_optimized': Policy(self.send_shots_optimized, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'Optimizacion_ML': Policy(self.send_ML, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'Optimizacion_PD': Policy(self.send_PD, self.max_qubits, self.time_limit_seconds , self.executeCircuit, self.machine_aws, self.machine_ibm),
                        'Topology_Compressed': Policy(self.send_topology_compressed, self.max_qubits, self.time_limit_seconds, self.executeCircuitCompressed, self.machine_aws, self.machine_ibm),
                        'Compressed_ML_Knapsack': Policy(self.send_compressed_ml_knapsack, self.max_qubits, self.time_limit_seconds, self.executeCircuit, self.machine_aws, self.machine_ibm),}
        
        self.translator = f"http://{self.app.config['TRANSLATOR']}:{self.app.config['TRANSLATOR_PORT']}/code/"
        self.unscheduler = f"http://{self.app.config['HOST']}:{self.app.config['PORT']}/unscheduler"
        self.app.route('/service/<service_name>', methods=['POST'])(self.service)
        

    def service(self, service_name:str) -> tuple:
        """
        The request handler, adding the circuit to the selected queue

        Args:
            service_name (str): The name of the service

        Request Parameters:
            circuit (str): The circuit to execute
            num_qubits (int): The number of qubits of the circuit            
            shots (int): The number of shots of the circuit            
            user (str): The user that executed the circuit
            circuit_name (str): The name of the circuit            
            maxDepth (int): The depth of the circuit            
            provider (str): The provider of the circuit

        Returns:
            tuple: The response of the request
        """
        if service_name not in self.services:
            return 'This service does not exist', 404
        circuit = request.json['circuit']
        num_qubits = request.json['num_qubits']
        shots = request.json['shots']
        user = request.json['user']
        circuit_name = request.json['circuit_name']
        maxDepth = request.json['maxDepth']
        provider = request.json['provider']
        iteracion = request.json['Iteracion']
        data = (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion)
        self.services[service_name].queues[provider].append(data)
        if not self.services[service_name].timers[provider].is_alive():
            self.services[service_name].timers[provider].start()
        n_qubits = sum(item[1] for item in self.services[service_name].queues[provider])
        if  n_qubits >= self.max_qubits and (service_name != 'Optimizacion_ML' and service_name != 'Optimizacion_PD' and service_name != 'Topology_Compressed' and service_name != 'Compressed_ML_Knapsack'):
            self.services[service_name].timers[provider].execute_and_reset()
        return 'Data received', 200
        
    
    def executeCircuit(self,data:dict,qb:list,shots:list,provider:str,urls:list, machine:str) -> None: #Data is the composed circuit to execute, qb is the number of qubits per circuit, shots is the number of shots per circut, provider is the provider of the circuit, urls is the array with data of each circuit (url, num_qubits, shots, user, circuit_name)
        """
        Executes the circuit in the selected provider

        Args:
            data (dict): The data of the circuit to execute            
            qb (list): The number of qubits per circuit            
            shots (list): The number of shots per circuit
            provider (str): The provider of the circuit            
            urls (list): The data of each circuit            
            machine (str): The machine to execute the circuit

        Raises:
            Exception: If an error occurs during the execution of the circuit
        """

        circuit = ''
        for data in json.loads(data)['code']:
            circuit = circuit + data + '\n'
        
        loc = {}
        if provider == 'ibm':
            loc['circuit'] = self.executeCircuitIBM.code_to_circuit_ibm(circuit)
        else:
            loc['circuit'] = code_to_circuit_aws(circuit)


        #circuit = 'def circ():\n'
        #f = json.loads(data)
        #for line in f['code']: #Construir el circuito según lo obtenido del traductor
        #    circuit = circuit + '\t' + line + '\n'
#
        #circuit = circuit + 'circuit = circ()'
#
        #print(circuit)
#
        #loc = {}
        #exec(circuit,globals(),loc) #Recuperar el objeto circuito que se obtiene, cuidado porque si el código del circuito no está controlado, esto es muy peligroso
        # Aquí se podría comprobar la mejor máquina para ejecutar el circuito
        try:
            if provider == 'ibm':
                #backend = least_busy_backend_ibm(sum(qb))
                # TODO escoger el backend más adecuado para el circuito
                #counts = runIBM(self.machine_ibm,loc['circuit'],max(shots)) #Ejecutar el circuito y obtener el resultado
                counts = self.executeCircuitIBM.runIBM_save(machine,loc['circuit'],max(shots),[url[3] for url in urls],qb,[url[4] for url in urls]) #Ejecutar el circuito y obtener el resultado
            else:
                counts = runAWS_save(machine,loc['circuit'],max(shots),[url[3] for url in urls],qb,[url[4] for url in urls],'') #Ejecutar el circuito y obtener el resultado
        except Exception as e:
            print(f"Error executing circuit: {e}")

        print(counts.items())

        data = {"counts": counts, "shots": shots, "provider": provider, "qb": qb, "users": [url[3] for url in urls], "circuit_names": [url[4] for url in urls]}

        requests.post(self.unscheduler, json=data)


    def most_repetitive(self, array:list) -> int: #Check the most repetitive element in an array and if there are more than one, return the smallest
        """
        Returns the most repetitive element in an array

        Args:
            array (list): The array to check
        
        Returns:
            int: The most repetitive element in the array
        """
        count_dict = {}
        for element in array: #Hashing the elements and counting them
            if element in count_dict:
                count_dict[element] += 1
            else:
                count_dict[element] = 1

        max_count = 0
        max_element = None
        for element, count in count_dict.items(): #Simple search for the higher element in the hash. If two elements have the same count, the smallest is returned
            if count > max_count or (count == max_count and element < max_element):
                max_count = count
                max_element = element

        return max_element
    
    def get_ibm_queue_length(self) -> int:
        """
        Obtiene el número de trabajos en espera en la cola de IBM.
        """


        try:
            self.transpile_lock = threading.Lock()
            self.condition = threading.Condition()
            self.service = QiskitRuntimeService()
            all_jobs = self.service.jobs()
            queued_jobs = self.queued_jobs = len([job for job in all_jobs if job.status() == qiskit.providers.JobStatus.QUEUED])
            print(f"🔎 IBM Job Queue: {queued_jobs} trabajos en espera")
            return queued_jobs
        except Exception as e:
            print(f"⚠️ Error obteniendo la cola de IBM: {e}")
            return 0  # Si hay un error, asumimos que no hay trabajos en cola

    def create_circuit(self,urls:list,code:list,qb:list,provider:str) -> None: #TODO add there the returning queue and in the other methods, check if the queue is not empty after the execution of thid method, so it adds the circuits back
        """
        Creates the circuit to execute based on the URLs

        Args:
            urls (list): The data of each circuit            
            code (list): The code of the composed circuit            
            qb (list): The number of qubits of each individual circuit            
            provider (str): The provider of the circuit
        """
        composition_qubits = 0
        for item in urls:
            # Manejar tanto tuplas de 6 elementos (sin iteracion) como de 7 (con iteracion)
            if len(item) == 7:
                url, num_qubits, shots, user, circuit_name, depth, Iterator = item
            else:
                url, num_qubits, shots, user, circuit_name, depth = item
                Iterator = 0  # Valor por defecto
        #Change the q[...] and c[...] to q[composition_qubits+...] and c[composition_qubits+...]
            if 'algassert' in url: 
                # Send a request to the translator, in the post, the field url will be url and the field d will be composition_qubits
                try: # TODO, if error, maybe add the url to another list or something so its added after this to the waiting_urls queue
                    x = requests.post(self.translator+provider+'/individual', json = {'url':url, 'd':composition_qubits})
                except:
                    print("Error in the request to the translator")
                    # Add the url to the returning queue
                data = json.loads(x.text)
                for elem in data['code']:
                    code.append(elem)
            else:
                lines = url.split('\n')
                for i, line in enumerate(lines):
                    if provider == 'ibm':
                        line = line.replace('qreg_q[', f'qreg_q[{composition_qubits}+')
                        line = line.replace('creg_c[', f'creg_c[{composition_qubits}+')
                    elif provider == 'aws':
                        # In the AWS case, all elements have circuit. the integer elements in this line will be replaced by the element+composition_qubits
                        #line = re.sub(r'circuit\.(\w+)\(([\d, ]+)\)', lambda x: f'circuit.{x.group(1)}({", ".join(str(int(num)+composition_qubits) for num in x.group(2).split(","))})', line)
                        gate_name = re.search(r'circuit\.(.*?)\(', line).group(1)
                        if gate_name in ['rx', 'ry', 'rz', 'gpi', 'gpi2', 'phaseshift']:
                            # These gates have a parameter
                            # Edit the first parameter
                            line = re.sub(rf'{gate_name}\(\s*(\d+)', lambda m: f"{gate_name}({int(m.group(1)) + composition_qubits}", line, count=1)
                        elif gate_name in ['xx', 'yy', 'zz','ms'] or 'cphase' in gate_name:
                            # These gates have 2 parameters
                            # Edit the first and second parameters
                            line= re.sub(rf'{gate_name}\((\d+),\s*(\d+)', lambda m: f"{gate_name}({int(m.group(1)) + composition_qubits},{int(m.group(2)) + composition_qubits}", line, count=1)

                        else:
                            # These gates have no parameters, so change the number of qubits on all
                            line = re.sub(r'(\d+)', lambda m: str(int(m.group(1)) + composition_qubits), line)
                    code.append(line)
            composition_qubits += num_qubits
            qb.append(num_qubits)

        if provider == 'ibm':
            # Add at the first position of the code[]
            code.insert(0,"circuit = QuantumCircuit(qreg_q, creg_c)")
            code.insert(0, f"creg_c = ClassicalRegister({composition_qubits}, 'creg_c')")  # Usar 'creg_c' como nombre del registro
            code.insert(0, f"qreg_q = QuantumRegister({composition_qubits}, 'q')")  # Set composition_qubits as the number of classical bits
            code.insert(0,"from numpy import pi")
            code.insert(0,"import numpy as np")
            code.insert(0,"from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit")
            code.insert(0,"from qiskit.circuit.library import MCXGate, MCMT, XGate, YGate, ZGate")
            code.append("return circuit")
        # Para hacer urls y circuitos quizas sea posible hacer que las urls tengan en mismo formato de salida del traductor y se pueda hacer un solo metodo para ambos. Que no se sepa cuando salgan del traductor si es un circuito o una url, que se pueda hacer el mismo tratamiento a ambos
        elif provider == 'aws':
            code.insert(0,"circuit = Circuit()")
            code.insert(0,"from numpy import pi")
            code.insert(0,"import numpy as np")
            code.insert(0,"from collections import Counter")
            code.insert(0,"from braket.circuits import Circuit")
            code.append("return circuit")


    def send_shots_optimized(self,queue:list, max_qubits:int, provider:str, executeCircuit:Callable, machine:str) -> None:
        """
        Sends the URLs to the server with the minimum number of shots using the shots_optimized policy

        Args:
            queue (list): The waiting list
            max_qubits (int): The maximum number of qubits            
            provider (str): The provider of the circuit            
            executeCircuit (Callable): The function to execute the circuit            
            machine (str): The machine to execute the circuit
        """
        if len(queue) != 0:
            # Send the URLs to the server
            qb = []
            sumQb = 0
            urls = []
            iterator = queue.copy()
            iterator = sorted(iterator, key=lambda x: x[2]) #Sort the waiting list by shots ascending
            minShots = self.most_repetitive([url[2] for url in iterator]) #Get the most repetitive number of shots in the waiting list
            for url in iterator:
                if url[1]+sumQb <= max_qubits and url[2] >= minShots:
                    sumQb = sumQb + url[1]
                    urls.append(url)
                    index = queue.index(url)
                    #Reduce number of shots of the url in waiting_url instead of removing it
                    if queue[index][2] - minShots <= 0: #If the url has no shots left, remove it from the waiting list
                        queue.remove(url)
                    else:
                        old_tuple = queue[index]
                        new_tuple = old_tuple[:2] + (old_tuple[2] - minShots,) + old_tuple[3:]
                        queue[index] = new_tuple
            print(f"Sending {len(urls)} URLs to the server")
            print(urls)
            # Convert the dictionary to JSON
            code,qb = [],[]
            shotsUsr = [minShots] * len(urls) # The shots for all will be the most repetitive number of shots in the waiting list
            self.create_circuit(urls,code,qb,provider)
            data = {"code":code}
            Thread(target=executeCircuit, args=(json.dumps(data),qb,shotsUsr,provider,urls,machine)).start()
            #executeCircuit(json.dumps(data),qb,shotsUsr,provider,urls)
            self.services['shots_optimized'].timers[provider].reset()




    def send_ML(self, queue: list, max_qubits: int, provider: str, executeCircuit: Callable, machine: str) -> None:
        """
        Ejecuta la política de optimización basada en Machine Learning,
        asegurando que no se envíen circuitos si la cola de IBM ya tiene 3 o más trabajos en espera.
        """
        print("Ejecutando política ML...")
        start_time = time.process_time()  # Iniciar el timer


        if not queue:
            print("⚠️ La cola está vacía, deteniendo temporizador.")
            self.services['Optimizacion_ML'].timers[provider].stop()
            return

        if provider == 'ibm':
            # 1. Verificar la cola de IBM antes de ejecutar cualquier circuito
            while self.get_ibm_queue_length() >= 3:
                print("⏳ La cola de IBM tiene 3 o más trabajos en espera. Esperando para enviar circuitos...")
                time.sleep(10)  # Esperamos 10 segundos antes de volver a verificar

            ibm_queue_length = self.get_ibm_queue_length()
            ##print(f"✅ La cola de IBM tiene {ibm_queue_length} trabajos en espera. Continuando con la ejecución.")

        # 2. Formatear la cola para ML
        formatted_queue = [(str(user), num_qubits, iteracion) for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in queue]
        print(f"📌 Cola formateada para ML: {formatted_queue}")

        # 3. Selección de circuitos usando ML o incluyendo todos si caben
        total_qb = sum(item[1] for item in formatted_queue)
        seleccionados, _, nueva_cola = optimizar_espacio_ml(self.model, formatted_queue, max_qubits, self.forced_threshold)

        max_qbits = sum(item[1] for item in seleccionados)
        ##print(f"✅ Elementos seleccionados en ML: {seleccionados}")
        ##print(f"🔢 Suma total de qubits seleccionados: {max_qbits}")

        # 4. Si no hay elementos seleccionados, detenemos la ejecución
        if not seleccionados:
            print("⚠️ No se han seleccionado elementos, deteniendo ejecución.")
            self.services['Optimizacion_ML'].timers[provider].stop()
            return

        # 5. Obtener los IDs seleccionados
        seleccionados_ids = {str(s[0]) for s in seleccionados}

        # 6. Filtrar los circuitos completos correspondientes a los IDs seleccionados
        seleccionados_completos = [item for item in queue if str(item[3]) in seleccionados_ids]

        # 7. Formatear los datos para create_circuit
        urls_for_create = [(circuit, num_qubits, shots, user, circuit_name, maxDepth) for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in seleccionados_completos]

      # 8. Actualizar la cola: eliminar elementos procesados y aumentar la prioridad de los que no se procesaron
        queue[:] = [
            (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion + 1)
            for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in queue
                if str(user) not in seleccionados_ids

]

        # **Verificar si los elementos realmente se eliminaron**
        elementos_restantes = [item for item in queue if str(item[3]) in seleccionados_ids]
        if elementos_restantes:
            print(f"⚠️ ERROR: Estos elementos NO se eliminaron correctamente: {elementos_restantes}")

        # **9. Ejecutar los circuitos seleccionados en un solo hilo para evitar concurrencia descontrolada**
        """
        if urls_for_create:
            code, qb = [], []
            shotsUsr = [item[2] for item in urls_for_create]
            self.create_circuit(urls_for_create, code, qb, provider)
            data = {"code": code}

            Thread(target=executeCircuit, args=(json.dumps(data), qb, shotsUsr, provider, urls_for_create, machine)).start()"""
        
        end_time = time.process_time()  # Finalizar el timer
        elapsed_time = end_time - start_time  # Calcular el tiempo transcurrido
        print(f"Tiempo de ejecución de send: {elapsed_time:.6f} segundos en ML")

        with open("./SalidaML.txt", 'a') as file:
            file.write("Cola Formateada:")
            file.write(str(formatted_queue))
            file.write("\n")
            file.write("Cola Seleccionada:")
            file.write(str(seleccionados))
            file.write("\n")
            file.write("Qbits alcanzados: ")
            file.write(str(max_qbits))  
            file.write("\n")
            file.write("Tiempo Ejecucion:")
            file.write(str(elapsed_time))
            file.write("\n")



        # **10. Verificar si la cola está vacía antes de reiniciar el temporizador**
        if not queue:
            print("✅ Cola vacía después de ejecución, deteniendo temporizador.")
            self.services['Optimizacion_ML'].timers[provider].stop()
        else:
            ##print("🔁 La cola aún tiene elementos, reiniciando temporizador.")
            self.services['Optimizacion_ML'].timers[provider].reset()

    def send_PD(self, queue: list, max_qubits: int, provider: str, executeCircuit: Callable, machine: str) -> None:
        """
        Ejecuta la política de optimización basada en Programacion Dinamica.
        """
        print("Ejecutando política Programación Dinamica...")
        start_time = time.process_time()  # Iniciar el timer


        if not queue:
            print("⚠️ La cola está vacía, deteniendo temporizador.")
            self.services['Optimizacion_PD'].timers[provider].stop()
            return
        
                # 1. Verificar la cola de IBM antes de ejecutar cualquier circuito
        while self.get_ibm_queue_length() >= 3:
            print("⏳ La cola de IBM tiene 3 o más trabajos en espera. Esperando para enviar circuitos...")
            time.sleep(10)  # Esperamos 10 segundos antes de volver a verificar

        ibm_queue_length = self.get_ibm_queue_length()
        ##print(f"✅ La cola de IBM tiene {ibm_queue_length} trabajos en espera. Continuando con la ejecución.")

        # 1. Formatear la cola para ML
        formatted_queue = [(str(user), num_qubits, iteracion) for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in queue]
        print(f"📌 Cola formateada para PD: {formatted_queue}")


        # 2. Selección de circuitos usando ML o incluyendo todos si caben
        total_qb = sum(item[1] for item in formatted_queue)
        #if total_qb <= max_qubits:
         #   print("📌 Todos los elementos caben en la capacidad. Se seleccionan todos.")
         #   seleccionados = formatted_queue
         #   nueva_cola = []
        # else:
        seleccionados, _, nueva_cola = optimizar_espacio_dinamico( formatted_queue, max_qubits, self.forced_threshold)
        
        max_qbits = sum(item[1] for item in seleccionados)

        # 3. Si no hay elementos seleccionados, detenemos la ejecución
        if not seleccionados:
            print("⚠️ No se han seleccionado elementos, deteniendo ejecución.")
            self.services['Optimizacion_PD'].timers[provider].stop()
            return

        # 4. Obtener los IDs seleccionados
        seleccionados_ids = {str(s[0]) for s in seleccionados}
        # print(f"📌 IDs seleccionados: {seleccionados_ids}")

        # 5. Filtrar los circuitos completos correspondientes a los IDs seleccionados
        seleccionados_completos = [item for item in queue if str(item[3]) in seleccionados_ids]
        # print(f"🚀 Elementos seleccionados completos PD: {seleccionados_completos}")

        # 6. Formatear los datos para create_circuit
        urls_for_create = [(circuit, num_qubits, shots, user, circuit_name, maxDepth) for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in seleccionados_completos]

        # **7. Eliminar de la cola ANTES de ejecutar `executeCircuit`**
        queue[:] = [
            (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion + 1)
            for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in queue
                if str(user) not in seleccionados_ids]
        #queue[:] = [item for item in queue if str(item[3]) not in seleccionados_ids]

        # **Verificar si los elementos realmente se eliminaron**
        elementos_restantes = [item for item in queue if str(item[3]) in seleccionados_ids]
        if elementos_restantes:
            print(f"⚠️ ERROR: Estos elementos NO se eliminaron correctamente: {elementos_restantes}")

        # **8. Ejecutar los circuitos seleccionados en un solo hilo para evitar concurrencia descontrolada**
        """
        if urls_for_create:
            code, qb = [], []
            shotsUsr = [item[2] for item in urls_for_create]
            self.create_circuit(urls_for_create, code, qb, provider)
            data = {"code": code}


            Thread(target=executeCircuit, args=(json.dumps(data), qb, shotsUsr, provider, urls_for_create, machine)).start()"""
        end_time = time.process_time()  # Finalizar el timer
        elapsed_time = end_time - start_time  # Calcular el tiempo transcurrido
        ##print(f"Tiempo de ejecución de send: {elapsed_time:.6f} segundos")

        with open("./SalidaPD.txt", 'a') as file:
            file.write("Cola Formateada:")
            file.write(str(formatted_queue))
            file.write("\n")
            file.write("Cola Seleccionada:")
            file.write(str(seleccionados))
            file.write("\n")
            file.write("Qbits alcanzados: ")
            file.write(str(max_qbits))  
            file.write("\n")
            file.write("Tiempo Ejecucion:")
            file.write(str(elapsed_time))
            file.write("\n")

        # **9. Verificar si la cola está vacía antes de reiniciar el temporizador**
        if not queue:
            ##print("✅ Cola vacía después de ejecución, deteniendo temporizador.")
            self.services['Optimizacion_PD'].timers[provider].stop()
        else:
            ##print("🔁 La cola aún tiene elementos, reiniciando temporizador.")
            self.services['Optimizacion_PD'].timers[provider].reset()     








    def send_shots_depth(self,queue:list, max_qubits:int, provider:str, executeCircuit:Callable, machine:str) -> None:
        """
        Sends the URLs to the server with the minimum number of shots and similar depth using the shots_depth policy

        Args:
            queue (list): The waiting list            
            max_qubits (int): The maximum number of qubits            
            provider (str): The provider of the circuit            
            executeCircuit (Callable): The function to execute the circuit            
            machine (str): The machine to execute the circuit
        """
        # Send the URLs to the server
        if len(queue) != 0:
            qb = []
            sumQb = 0
            urls = []
            iterator = queue.copy()
            iterator = sorted(iterator, key=lambda x: x[2]) #Sort the waiting list by shots ascending
            minShots = iterator[0][2] #Get the minimum number of shots in the waiting list
            depth = iterator[0][5] #Get the depth of the first url in the waiting list
            for url in iterator:
                if url[1]+sumQb <= max_qubits and url[5] <= depth * 1.1 and url[5] >= depth * 0.9:
                    sumQb = sumQb + url[1]
                    urls.append(url)
                    index = queue.index(url)
                    #Reduce number of shots of the url in waiting_url instead of removing it
                    if queue[index][2] - minShots <= 0: #If the url has no shots left, remove it from the waiting list
                        queue.remove(url)
                    else:
                        old_tuple = queue[index]
                        new_tuple = old_tuple[:2] + (old_tuple[2] - minShots,) + old_tuple[3:]
                        queue[index] = new_tuple
            print(f"Sending {len(urls)} URLs to the server")
            print(urls)
            code,qb = [],[]
            shotsUsr = [minShots] * len(urls) # The shots for all will be the minimum number of shots in the waiting list
            self.create_circuit(urls,code,qb,provider)
            data = {"code":code}
            Thread(target=executeCircuit, args=(json.dumps(data),qb,shotsUsr,provider,urls,machine)).start()
            #executeCircuit(json.dumps(data),qb,shotsUsr,provider,urls)
            self.services['shots_depth'].timers[provider].reset()

    def send_depth(self,queue:list, max_qubits:int, provider:str, executeCircuit:Callable, machine:str) -> None:
        """
        Sends the URLs to the server with the most similar depth using the depth policy

        Args:
            queue (list): The waiting list
            max_qubits (int): The maximum number of qubits            
            provider (str): The provider of the circuit            
            executeCircuit (Callable): The function to execute the circuit            
            machine (str): The machine to execute the circuit
        """
        # Send the URLs to the server
        if len(queue) != 0:
            print('Sent')
            qb = []
            # Convert the dictionary to JSON
            urls = []
            sumQb = 0
            depth = queue[0][5] #Get the depth of the first url in the waiting list
            iterator = queue.copy()
            iterator = iterator[:1] + sorted(iterator[1:], key=lambda x: abs(x[5] - depth)) #Sort the waiting list by difference in depth by the first circuit in the waiting list so it picks the most similar circuit (dont sort the first element because is the reference for the calculation)
            for url in iterator: #Add them to the valid_url only if they fit and are similar to the first circuit in the waiting list
                if url[1]+ sumQb <= max_qubits and url[5] <= depth * 1.1 and url[5] >= depth * 0.9:
                    urls.append(url)
                    sumQb += url[1]
                    queue.remove(url)
            print(f"Sending {len(urls)} URLs to the server")
            print(urls)
            code,qb = [],[]
            shotsUsr = [url[2] for url in urls] #Each one will have its own number of shots, a statistic will be used to get the results after
            self.create_circuit(urls,code,qb,provider)
            data = {"code":code}
            Thread(target=executeCircuit, args=(json.dumps(data),qb,shotsUsr,provider,urls,machine)).start()
            #executeCircuit(json.dumps(data),qb,shotsUsr,provider,urls)
            self.services['depth'].timers[provider].reset()

    def send_shots(self,queue:list, max_qubits:int, provider:str, executeCircuit:Callable, machine:str) -> None:
        """
        Sends the URLs to the server with the minimum number of shots using the shots policy

        Args:
            queue (list): The waiting list            
            max_qubits (int): The maximum number of qubits            
            provider (str): The provider of the circuit            
            executeCircuit (Callable): The function to execute the circuit            
            machine (str): The machine to execute the circuit
        """
        # Send the URLs to the server
        if len(queue) != 0:
            print('Sent')
            qb = []
            sumQb = 0
            urls = []
            iterator = queue.copy()
            iterator = sorted(iterator, key=lambda x: x[2]) #Sort the waiting list by shots ascending
            minShots = iterator[0][2] #Get the minimum number of shots in the waiting list
            for url in iterator:
                if url[1]+sumQb <= max_qubits:
                    sumQb = sumQb + url[1]
                    urls.append(url)
                    index = queue.index(url)
                    #Reduce number of shots of the url in waiting_url instead of removing it
                    if queue[index][2] - minShots <= 0: #If the url has no shots left, remove it from the waiting list
                        queue.remove(url)
                    else:
                        old_tuple = queue[index]
                        new_tuple = old_tuple[:2] + (old_tuple[2] - minShots,) + old_tuple[3:]
                        queue[index] = new_tuple
            code,qb = [],[]
            shotsUsr = [minShots] * len(urls) # All the urls will have the minimum number of shots in the waiting list
            self.create_circuit(urls,code,qb,provider)
            data = {"code":code}
            Thread(target=executeCircuit, args=(json.dumps(data),qb,shotsUsr,provider,urls,machine)).start() #Parece que sin esto no se resetea el timer cuando termina de componer
            #executeCircuit(json.dumps(data),qb,shotsUsr,provider,urls)
            self.services['shots'].timers[provider].reset()

    def send(self,queue:list, max_qubits:int, provider:str, executeCircuit:Callable, machine:str) -> None:
        """
        Sends the URLs to the server using the time policy

        Args:
            queue (list): The waiting list            
            max_qubits (int): The maximum number of qubits            
            provider (str): The provider of the circuit            
            executeCircuit (Callable): The function to execute the circuit            
            machine (str): The machine to execute the circuit
        """
        start_time = time.process_time()  # Iniciar el timer


        if len(queue) != 0:
            print('Sent')
            urls = []
            iterator = queue.copy() #Make a copy to not delete on search
            sumQb = 0
            for url in iterator:
                if url[1] + sumQb <= max_qubits: #Shots of current url + shots of all the urls on urls
                    urls.append(url)
                    sumQb += url[1]
                    queue.remove(url)
            code,qb = [],[]
            print("sumQb", sumQb)
            shotsUsr = [10000] * len(urls)
            #shotsUsr = [url[2] for url in urls] # Each url will have its own number of shots, a statistic will be used to get the results after
            self.create_circuit(urls,code,qb,provider)
            data = {"code":code}

            """
            Thread(target=executeCircuit, args=(json.dumps(data),qb,shotsUsr,provider,urls,machine)).start()"""

            end_time = time.process_time()  # Finalizar el timer
            elapsed_time = end_time - start_time  # Calcular el tiempo transcurrido
            print(f"Tiempo de ejecución de send: {elapsed_time:.6f} segundos en Tiempo")

            with open("./SalidaTime.txt", 'a') as file:
                file.write("Suma de qBits:")
                file.write(str(sumQb))
                file.write("\n")
                file.write("Tiempo Ejecucion:")
                file.write(str(elapsed_time))
                file.write("\n")


            #executeCircuit(json.dumps(data),qb,shotsUsr,provider,urls)
            self.services['time'].timers[provider].reset()

    
    def executeCircuitCompressed(self, data: dict, qb: list, shots: list, provider: str, urls: list, machine: str) -> None:
        """
        Ejecuta el circuito aplicando compresión de topología antes de la ejecución.
        
        Args:
            data (dict): Los datos del circuito a ejecutar
            qb (list): El número de qubits por circuito
            shots (list): El número de shots por circuito
            provider (str): El proveedor del circuito (ibm o aws)
            urls (list): Los datos de cada circuito
            machine (str): La máquina en la que ejecutar el circuito
        
        Raises:
            Exception: Si ocurre un error durante la ejecución del circuito
        """
        print("\n" + "="*80)
        print(f"🔄 INICIANDO EJECUCIÓN CON COMPRESIÓN DE TOPOLOGÍA")
        print(f"📊 Provider: {provider.upper()} | Machine: {machine}")
        print("="*80)
        
        # 1. Convertir código string a QuantumCircuit
        circuit_code = ''
        for line in json.loads(data)['code']:
            circuit_code = circuit_code + line + '\n'
        
        print(f"\n📝 Código del circuito compuesto recibido ({len(circuit_code)} caracteres)")
        
        loc = {}
        original_qubits = sum(qb)
        
        print(f"\n🔢 Circuito original: {original_qubits} qubits lógicos totales")
        print(f"   📦 Compuesto por {len(qb)} circuitos individuales: {qb}")
        
        # 2. Convertir a objeto QuantumCircuit según el provider
        try:
            if provider == 'ibm':
                print(f"\n⚙️  Convirtiendo código a QuantumCircuit (IBM)...")
                loc['circuit'] = self.executeCircuitIBM.code_to_circuit_ibm(circuit_code)
                print(f"✅ Circuito IBM creado exitosamente")
                print(f"   - Qubits: {loc['circuit'].num_qubits}")
                print(f"   - Depth: {loc['circuit'].depth()}")
                print(f"   - Gates: {len(loc['circuit'].data)}")
            else:
                print(f"\n⚙️  Convirtiendo código a Circuit (AWS)...")
                loc['circuit'] = code_to_circuit_aws(circuit_code)
                print(f"✅ Circuito AWS creado exitosamente")
        except Exception as e:
            print(f"\n❌ ERROR al convertir código a circuito: {e}")
            raise
        
        # 3. Aplicar compresión de topología
        print(f"\n🗜️  APLICANDO COMPRESIÓN DE TOPOLOGÍA...")
        print(f"   Compresor: {'IBM' if provider == 'ibm' else 'AWS'}")
        
        try:
            compressor = self.compressor_ibm if provider == 'ibm' else self.compressor_aws
            
            print(f"   └─ Paso 1: Adelantando medidas y optimizando...")
            compressed_circuit = compressor.compress_and_map(loc['circuit'])
            
            # Validar que la compresión retornó un circuito válido
            if compressed_circuit is None:
                print(f"\n⚠️  ERROR: El compresor retornó None")
                print(f"   Continuando con circuito sin comprimir...")
            else:
                compressed_qubits = compressed_circuit.num_qubits
                compression_ratio = (1 - compressed_qubits / original_qubits) * 100 if original_qubits > 0 else 0
                
                print(f"\n✨ COMPRESIÓN COMPLETADA:")
                print(f"   📉 Qubits: {original_qubits} → {compressed_qubits}")
                print(f"   💾 Ahorro: {compression_ratio:.1f}%")
                print(f"   📏 Depth comprimido: {compressed_circuit.depth()}")
                print(f"   🚪 Gates comprimidos: {len(compressed_circuit.data)}")
                
                # Guardar información de circuitos comprimidos en archivo
                try:
                    with open("./CircuitosComprimidos.txt", 'a', encoding='utf-8') as file:
                        file.write(f"\n{'='*70}\n")
                        file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        file.write(f"Provider: {provider.upper()} | Machine: {machine}\n")
                        file.write(f"Tipo: Compresión de topología (batch)\n")
                        file.write(f"Circuitos comprimidos juntos: {len(urls)}\n")
                        file.write(f"Qubits: {original_qubits} → {compressed_qubits} (Ahorro: {compression_ratio:.1f}%)\n")
                        file.write(f"Depth: {compressed_circuit.depth()} | Gates: {len(compressed_circuit.data)}\n")
                        file.write(f"\nCircuitos en el batch:\n")
                        for i, (circuit, num_qubits, shot, user, circuit_name, maxDepth) in enumerate(urls, 1):
                            file.write(f"  {i}. ID: {user} | {circuit_name} | {num_qubits} qubits\n")
                        file.write(f"{'='*70}\n")
                    print(f"   💾 Información guardada en CircuitosComprimidos.txt")
                except Exception as e:
                    print(f"   ⚠️ Error al guardar información: {e}")
                
                # Reemplazar el circuito original con el comprimido
                loc['circuit'] = compressed_circuit
            
        except Exception as e:
            print(f"\n⚠️  ERROR en compresión: {e}")
            print(f"   Continuando con circuito sin comprimir...")
            import traceback
            traceback.print_exc()  # Mostrar traceback completo para debugging
        
        # 4. Ejecutar el circuito (comprimido o no)
        print(f"\n🚀 EJECUTANDO CIRCUITO EN {provider.upper()}...")
        print(f"   Machine: {machine}")
        print(f"   Shots: {max(shots)}")
        
        # Ejecución real en IBM/AWS
        try:
            if provider == 'ibm':
                print(f"   🚀 EJECUTANDO en IBM Quantum...")
                counts = self.executeCircuitIBM.runIBM_save(
                    machine, 
                    loc['circuit'], 
                    max(shots),
                    [url[3] for url in urls], 
                    qb, 
                    [url[4] for url in urls]
                )
            else:
                print(f"   🚀 EJECUTANDO en AWS Braket...")
                counts = runAWS_save(
                    machine, 
                    loc['circuit'], 
                    max(shots),
                    [url[3] for url in urls], 
                    qb, 
                    [url[4] for url in urls], 
                    ''
                )
            
            print(f"\n✅ EJECUCIÓN COMPLETADA")
            print(f"   Resultados obtenidos: {len(counts)} estados")
            
        except Exception as e:
            print(f"\n❌ ERROR en ejecución: {e}")
            raise
        
        # 5. Enviar resultados al unscheduler
        print(f"\n📤 Enviando resultados al unscheduler...")
        print(f"   Users: {[url[3] for url in urls]}")
        print(f"   Circuit names: {[url[4] for url in urls]}")
        
        result_data = {
            "counts": counts, 
            "shots": shots, 
            "provider": provider, 
            "qb": qb,  # Mantenemos qb original para descomponer correctamente
            "users": [url[3] for url in urls], 
            "circuit_names": [url[4] for url in urls]
        }
        
        # Enviar resultados al unscheduler para guardar en MongoDB
        print(f"   📤 Enviando resultados a unscheduler...")
        requests.post(self.unscheduler, json=result_data)
        print(f"   ✅ Resultados enviados correctamente: {len(result_data['counts'])} estados")
        
        print(f"✅ Ejecución completada")
        print("="*80 + "\n")


    def send_topology_compressed(self, queue: list, max_qubits: int, provider: str, executeCircuit: Callable, machine: str) -> None:
        """
        Política de scheduling que aplica compresión de topología a los circuitos.
        Agrupa circuitos hasta llenar la capacidad y luego aplica compresión para 
        reducir el uso de qubits físicos.
        
        Args:
            queue (list): La cola de espera con circuitos pendientes
            max_qubits (int): El número máximo de qubits disponibles
            provider (str): El proveedor del circuito (ibm o aws)
            executeCircuit (Callable): La función para ejecutar el circuito (executeCircuitCompressed)
            machine (str): La máquina en la que ejecutar el circuito
        """
        print("\n" + "🔷"*40)
        print(f"🎯 POLÍTICA: TOPOLOGY COMPRESSED ({provider.upper()})")
        print("🔷"*40)
        
        start_time = time.process_time()
        
        if not queue:
            print("📭 Cola vacía, nada que procesar.")
            return
        
        print(f"\n📊 ESTADO INICIAL DE LA COLA:")
        print(f"   Circuitos en espera: {len(queue)}")
        print(f"   Capacidad máxima: {max_qubits} qubits")
        
        # Información detallada de cada circuito
        total_qubits_queue = sum(item[1] for item in queue)
        print(f"   Total qubits en cola: {total_qubits_queue}")
        print(f"\n   Detalles de circuitos:")
        for i, (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in enumerate(queue[:5], 1):
            print(f"      {i}. {circuit_name[:30]:30s} | {num_qubits:3d} qubits | User: {user}")
        if len(queue) > 5:
            print(f"      ... y {len(queue)-5} circuitos más")
        
        # Seleccionar circuitos que caben en max_qubits
        urls = []
        iterator = queue.copy()
        sumQb = 0
        
        print(f"\n🔍 SELECCIONANDO CIRCUITOS PARA BATCH...")
        for item in iterator:
            circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion = item
            if num_qubits + sumQb <= max_qubits:
                urls.append(item)
                sumQb += num_qubits
                # Eliminar de forma segura (protección contra race conditions)
                try:
                    queue.remove(item)
                    print(f"   ✓ Añadido: {circuit_name[:30]:30s} ({num_qubits:3d} qubits) → Total: {sumQb}/{max_qubits}")
                except ValueError:
                    # El item ya fue eliminado por otra ejecución concurrente
                    print(f"   ⚠️ Skippeado: {circuit_name[:30]:30s} (ya procesado por otro thread)")
                    urls.remove(item)  # También quitarlo de urls ya que no está en queue
                    sumQb -= num_qubits  # Revertir suma
        
        if not urls:
            print(f"\n⚠️  No se pudo seleccionar ningún circuito (el primero requiere más de {max_qubits} qubits)")
            return
        
        print(f"\n✅ BATCH SELECCIONADO:")
        print(f"   Circuitos a ejecutar: {len(urls)}")
        print(f"   Qubits lógicos totales: {sumQb}")
        print(f"   Utilización: {sumQb/max_qubits*100:.1f}%")
        print(f"   Circuitos restantes en cola: {len(queue)}")
        
        # Formatear URLs para create_circuit (sin el campo iteracion)
        urls_for_create = [(circuit, num_qubits, shots, user, circuit_name, maxDepth) 
                           for (circuit, num_qubits, shots, user, circuit_name, maxDepth, iteracion) in urls]
        
        # Crear el circuito compuesto
        code, qb = [], []
        shotsUsr = [item[2] for item in urls_for_create]
        
        print(f"\n🔨 CREANDO CIRCUITO COMPUESTO...")
        self.create_circuit(urls_for_create, code, qb, provider)
        data = {"code": code}
        print(f"   ✅ Circuito compuesto creado: {len(code)} líneas de código")
        
        # Ejecutar con compresión en un hilo
        print(f"\n🧵 Lanzando ejecución en thread separado...")
        Thread(target=executeCircuit, args=(json.dumps(data), qb, shotsUsr, provider, urls_for_create, machine)).start()
        
        end_time = time.process_time()
        elapsed_time = end_time - start_time
        
        print(f"\n⏱️  TIEMPO DE PROCESAMIENTO:")
        print(f"   Tiempo de scheduling: {elapsed_time:.6f} segundos")
        
        # Guardar métricas en archivo
        with open("./SalidaTopologyCompressed.txt", 'a') as file:
            file.write(f"\n{'='*60}\n")
            file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            file.write(f"Provider: {provider}\n")
            file.write(f"Circuitos procesados: {len(urls)}\n")
            file.write(f"Qubits lógicos totales: {sumQb}\n")
            file.write(f"Qubits por circuito: {qb}\n")
            file.write(f"Tiempo de scheduling: {elapsed_time:.6f} seg\n")
            file.write(f"Circuitos en cola restante: {len(queue)}\n")
        
        # Gestión del temporizador
        if not queue:
            print(f"\n✅ Cola vacía, deteniendo temporizador.")
            self.services['Topology_Compressed'].timers[provider].stop()
        else:
            print(f"\n🔁 Reiniciando temporizador ({len(queue)} circuitos restantes)...")
            self.services['Topology_Compressed'].timers[provider].reset()
        
        print("🔷"*40 + "\n")


    def send_compressed_ml_knapsack(self, queue: list, max_qubits: int, provider: str, executeCircuit: Callable, machine: str) -> None:
        """
        Política que comprime circuitos individualmente antes de aplicar mochila ML.
        
        Flujo:
        1. Timer de 10s se dispara
        2. Comprimir cada circuito individualmente
        3. Usar tamaños comprimidos para algoritmo ML de mochila
        4. Ejecutar circuitos YA comprimidos (sin re-comprimir)
        
        Args:
            queue (list): Cola de espera con circuitos pendientes
            max_qubits (int): Número máximo de qubits disponibles
            provider (str): Proveedor (ibm o aws)
            executeCircuit (Callable): Función para ejecutar circuitos
            machine (str): Máquina donde ejecutar
        """
        print("\n" + "🔶"*40)
        print(f"🎯 POLÍTICA: COMPRESSED ML KNAPSACK ({provider.upper()})")
        print("🔶"*40)
        
        start_time = time.process_time()
        
        if not queue:
            print("⚠️ La cola está vacía, deteniendo temporizador.")
            self.services['Compressed_ML_Knapsack'].timers[provider].stop()
            return
        
        # Verificar cola de IBM si es necesario
        if provider == 'ibm':
            while self.get_ibm_queue_length() >= 3:
                print("⏳ La cola de IBM tiene 3 o más trabajos. Esperando...")
                time.sleep(10)
        
        print(f"\n📊 ESTADO INICIAL:")
        print(f"   Circuitos en cola: {len(queue)}")
        print(f"   Capacidad máxima: {max_qubits} qubits")
        
        # PASO 1: COMPRIMIR INDIVIDUALMENTE CADA CIRCUITO
        print(f"\n🗜️  FASE 1: COMPRESIÓN INDIVIDUAL DE CIRCUITOS")
        print("="*60)
        
        compressor = self.compressor_ibm if provider == 'ibm' else self.compressor_aws
        compressed_data = []  # [(user, qubits_compressed, iteracion, circuit_compressed_code, original_item), ...]
        
        for idx, item in enumerate(queue, 1):
            circuit_url, num_qubits, shots, user, circuit_name, maxDepth, iteracion = item
            
            # Generar ID único: combinación de índice en cola + nombre del circuito
            unique_id = f"{idx}_{circuit_name}"
            
            print(f"\n   [{idx}/{len(queue)}] Comprimiendo: {circuit_name[:40]}")
            
            try:
                # PASO 1a: Descargar código desde GitHub
                if 'github' in circuit_url or 'raw.githubusercontent' in circuit_url:
                    # Es una URL de GitHub, descargar directamente
                    try:
                        response = requests.get(circuit_url, timeout=10)
                        if response.status_code == 200:
                            circuit_code = response.text
                        else:
                            print(f"        ❌ Error descargando GitHub (status {response.status_code})")
                            continue
                    except Exception as e:
                        print(f"        ❌ Error descargando desde GitHub: {e}")
                        continue
                elif 'algassert' in circuit_url:
                    # URL de algassert, usar traductor
                    try:
                        response = requests.post(
                            self.translator + provider + '/individual',
                            json={'url': circuit_url, 'd': 0},
                            timeout=10
                        )
                        if response.status_code == 200:
                            translated_data = json.loads(response.text)
                            circuit_code = '\n'.join(translated_data['code'])
                        else:
                            print(f"        ❌ Error en traductor (status {response.status_code})")
                            continue
                    except Exception as e:
                        print(f"        ❌ Error traduciendo URL: {e}")
                        continue
                else:
                    # Asumir que ya es código
                    circuit_code = circuit_url
                
                # PASO 1b: Convertir código a QuantumCircuit
                loc = {}
                try:
                    if provider == 'ibm':
                        loc['circuit'] = self.executeCircuitIBM.code_to_circuit_ibm(circuit_code)
                    else:
                        loc['circuit'] = code_to_circuit_aws(circuit_code)
                    
                    # Validar que el circuito se creó correctamente
                    if loc['circuit'] is None:
                        print(f"        ⚠️ No se pudo crear circuito desde el código")
                        continue
                except (ValueError, TypeError, AttributeError) as e:
                    print(f"        ⚠️ Formato de circuito no compatible, salteando...")
                    print(f"           Detalle: {str(e)[:100]}")
                    continue
                
                # Obtener qubits reales del circuito creado
                real_qubits = loc['circuit'].num_qubits
                real_depth = loc['circuit'].depth()
                real_gates = len(loc['circuit'].data)
                real_clbits = len(loc['circuit'].clbits)
                
                # Contar operaciones
                from collections import Counter
                real_ops = Counter(instr.operation.name for instr in loc['circuit'].data)
                has_measurements = 'measure' in real_ops
                
                print(f"        📋 ANTES: {real_qubits} qubits, {real_clbits} clbits, depth={real_depth}, gates={real_gates}")
                print(f"           Operaciones: {dict(real_ops)}")
                
                # Mostrar circuito original (solo si es pequeño)
                if real_qubits <= 8 and real_depth <= 20:
                    try:
                        print(f"\n        🎨 Circuito Original:")
                        circuit_drawing = loc['circuit'].draw(output='text', fold=-1)
                        for line in str(circuit_drawing).split('\n'):
                            print(f"           {line}")
                    except Exception as e:
                        print(f"           ⚠️ No se pudo dibujar: {e}")
                
                # PASO 1c: Aplicar compresión
                compressed_circuit = compressor.compress_and_map(loc['circuit'])
                
                if compressed_circuit is None:
                    print(f"        ⚠️ Compresión falló, usando original")
                    compressed_qubits = real_qubits
                    compressed_code = loc['circuit']  # Guardar circuito original como objeto
                else:
                    compressed_qubits = compressed_circuit.num_qubits
                    compressed_depth = compressed_circuit.depth()
                    compressed_gates = len(compressed_circuit.data)
                    compressed_clbits = len(compressed_circuit.clbits)
                    compression_ratio = (1 - compressed_qubits / real_qubits) * 100 if real_qubits > 0 else 0
                    
                    # Contar operaciones del comprimido
                    compressed_ops = Counter(instr.operation.name for instr in compressed_circuit.data)
                    
                    print(f"        📋 DESPUÉS: {compressed_qubits} qubits, {compressed_clbits} clbits, depth={compressed_depth}, gates={compressed_gates}")
                    print(f"           Operaciones: {dict(compressed_ops)}")
                    print(f"        ✅ Compresión: {real_qubits}→{compressed_qubits} qubits ({compression_ratio:.1f}%), {real_depth}→{compressed_depth} depth, {real_gates}→{compressed_gates} gates")
                    
                    # Guardar información de circuito comprimido en archivo
                    try:
                        with open("./CircuitosComprimidos.txt", 'a', encoding='utf-8') as file:
                            file.write(f"\n{'='*70}\n")
                            file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                            file.write(f"Provider: {provider.upper()}\n")
                            file.write(f"Tipo: Compresión individual (ML Knapsack)\n")
                            file.write(f"ID: {user} | Circuit: {circuit_name}\n")
                            file.write(f"Qubits: {real_qubits} → {compressed_qubits} (Ahorro: {compression_ratio:.1f}%)\n")
                            file.write(f"Depth: {real_depth} → {compressed_depth}\n")
                            file.write(f"Gates: {real_gates} → {compressed_gates}\n")
                            file.write(f"Classical bits: {real_clbits} → {compressed_clbits}\n")
                            file.write(f"{'='*70}\n")
                    except Exception as e:
                        print(f"        ⚠️ Error al guardar información: {e}")
                    
                    # Mostrar circuito comprimido (solo si es pequeño)
                    if compressed_qubits <= 8 and compressed_depth <= 20:
                        try:
                            print(f"\n        🎨 Circuito Comprimido:")
                            compressed_drawing = compressed_circuit.draw(output='text', fold=-1)
                            for line in str(compressed_drawing).split('\n'):
                                print(f"           {line}")
                        except Exception as e:
                            print(f"           ⚠️ No se pudo dibujar: {e}")
                    
                    # Guardar el circuito comprimido como objeto QuantumCircuit
                    compressed_code = compressed_circuit  # Guardar objeto, no string
                
                # Guardar datos comprimidos
                compressed_data.append({
                    'unique_id': unique_id,  # ID único para diferenciar circuitos
                    'queue_index': idx - 1,  # Índice original en la cola (enumerate empieza en 1)
                    'user': str(user),  # User original para enviar resultados
                    'qubits_compressed': compressed_qubits,
                    'iteracion': iteracion,
                    'compressed_code': compressed_code,
                    'shots': shots,
                    'circuit_name': circuit_name,
                    'maxDepth': maxDepth,
                    'original_qubits': real_qubits
                })
                
            except Exception as e:
                print(f"        ❌ Error procesando circuito: {str(e)[:100]}")
                # Si falla completamente, skip este circuito
                continue  # No agregar a compressed_data
        
        # PASO 2: FORMATEAR PARA ALGORITMO ML CON TAMAÑOS COMPRIMIDOS
        print(f"\n🤖 FASE 2: ALGORITMO ML DE MOCHILA")
        print("="*60)
        
        # Usar unique_id para que ML pueda diferenciar los circuitos
        formatted_queue = [(item['unique_id'], item['qubits_compressed'], item['iteracion']) 
                          for item in compressed_data]
        
        print(f"   Cola formateada (tamaños comprimidos): {len(formatted_queue)} circuitos")
        print(f"   Capacidad: {max_qubits} qubits")
        
        # PASO 3: APLICAR MOCHILA ML
        seleccionados, _, nueva_cola = optimizar_espacio_ml(
            self.model, 
            formatted_queue, 
            max_qubits, 
            self.forced_threshold
        )
        
        if not seleccionados:
            print("⚠️ No se seleccionaron elementos con ML.")
            print("   Incrementando prioridad de circuitos procesados y esperando siguiente ciclo...")
            
            # Crear set de índices procesados
            processed_indices = {item['queue_index'] for item in compressed_data}
            
            # Actualizar cola: incrementar iteración solo de los procesados
            new_queue = []
            for idx, item in enumerate(queue):
                if idx in processed_indices:
                    # Fue procesado pero no seleccionado, incrementar prioridad
                    circuit_url, num_qubits, shots, user, circuit_name, maxDepth, iteracion = item
                    new_queue.append((circuit_url, num_qubits, shots, user, circuit_name, maxDepth, iteracion + 1))
                else:
                    # No fue procesado (falló), mantener sin cambios
                    new_queue.append(item)
            
            queue[:] = new_queue
            
            # Reiniciar timer para intentar de nuevo
            self.services['Compressed_ML_Knapsack'].timers[provider].reset()
            return
        
        selected_qubits = sum(item[1] for item in seleccionados)
        print(f"\n   ✅ Seleccionados: {len(seleccionados)} circuitos")
        print(f"   📊 Qubits totales (comprimidos): {selected_qubits}/{max_qubits}")
        print(f"   📈 Utilización: {selected_qubits/max_qubits*100:.1f}%")
        
        # PASO 4: PREPARAR EJECUCIÓN (SIN RE-COMPRIMIR)
        print(f"\n🚀 FASE 3: PREPARACIÓN PARA EJECUCIÓN")
        print("="*60)
        
        seleccionados_ids = {str(s[0]) for s in seleccionados}
        
        # Filtrar circuitos comprimidos seleccionados (por unique_id)
        selected_compressed = [item for item in compressed_data 
                              if item['unique_id'] in seleccionados_ids]
        
        # COMPONER MANUALMENTE los circuitos comprimidos
        print(f"   🔨 Componiendo {len(selected_compressed)} circuitos comprimidos...")
        
        if provider == 'ibm':
            from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
            
            # Calcular qubits/cbits totales
            total_qubits = sum(item['qubits_compressed'] for item in selected_compressed)
            total_clbits = 0
            for item in selected_compressed:
                circuit = item['compressed_code']
                if circuit is not None:
                    total_clbits += len(circuit.clbits)
            
            qreg = QuantumRegister(total_qubits, 'q')
            creg = ClassicalRegister(total_clbits, 'creg_c')
            composed_circuit = QuantumCircuit(qreg, creg)
            
            # Componer circuitos con offset separados para qubits y cbits
            qubit_offset = 0
            clbit_offset = 0
            qb = []
            for item in selected_compressed:
                circuit = item['compressed_code']  # QuantumCircuit comprimido
                if circuit is not None:
                    # Mapear qubits y clbits del circuito actual al circuito compuesto
                    qubit_map = {circuit.qubits[i]: composed_circuit.qubits[qubit_offset + i] 
                                for i in range(len(circuit.qubits))}
                    clbit_map = {circuit.clbits[i]: composed_circuit.clbits[clbit_offset + i] 
                                for i in range(len(circuit.clbits))}
                    
                    # Agregar instrucciones del circuito comprimido
                    for instr, qargs, cargs in circuit.data:
                        mapped_qargs = [qubit_map[q] for q in qargs]
                        mapped_cargs = [clbit_map[c] for c in cargs]
                        composed_circuit.append(instr, mapped_qargs, mapped_cargs)
                    
                    qb.append(item['qubits_compressed'])
                    qubit_offset += item['qubits_compressed']
                    clbit_offset += len(circuit.clbits)
            
            print(f"   ✅ Circuito compuesto creado:")
            print(f"      - Total qubits: {composed_circuit.num_qubits}")
            print(f"      - Depth: {composed_circuit.depth()}")
            print(f"      - Gates: {len(composed_circuit.data)}")
            print(f"      - Qubits por circuito: {qb}")
            
        else:  # AWS
            # Para AWS, similar pero con Circuit de Braket
            print(f"   ⚠️ Composición manual para AWS no implementada aún")
            # TODO: Implementar para AWS si es necesario
            self.services['Compressed_ML_Knapsack'].timers[provider].stop()
            return
        
        # Actualizar cola: eliminar procesados, incrementar iteración de no procesados
        # Crear sets para identificar qué circuitos fueron procesados y seleccionados
        processed_indices = {item['queue_index'] for item in compressed_data}
        selected_indices = {item['queue_index'] for item in selected_compressed}
        
        new_queue = []
        for idx, item in enumerate(queue):
            if idx in selected_indices:
                # Este fue seleccionado y ejecutado, eliminar de cola
                continue
            elif idx in processed_indices:
                # Este fue procesado pero no seleccionado, incrementar prioridad
                circuit_code, num_qubits, shots, user, circuit_name, maxDepth, iteracion = item
                new_queue.append((circuit_code, num_qubits, shots, user, circuit_name, maxDepth, iteracion + 1))
            else:
                # Este no fue procesado (falló compresión), mantener sin cambios
                new_queue.append(item)
        
        queue[:] = new_queue
        
        # PASO 5: EJECUTAR CIRCUITO COMPUESTO (YA COMPRIMIDO)
        print(f"\n🚀 FASE 4: EJECUCIÓN")
        print("="*60)
        
        shotsUsr = [item['shots'] for item in selected_compressed]
        users = [item['user'] for item in selected_compressed]
        circuit_names = [item['circuit_name'] for item in selected_compressed]
        
        print(f"   🎯 Ejecutando circuito compuesto...")
        print(f"   📊 Shots: {max(shotsUsr)}")
        print(f"   🔢 Circuitos: {len(selected_compressed)}")
        
        # Ejecutar directamente con IBM (sin re-comprimir)
        try:
            print(f"   🚀 EJECUTANDO en {provider.upper()}...")
            counts = self.executeCircuitIBM.runIBM_save(
                machine, composed_circuit, max(shotsUsr), users, qb, circuit_names
            )
            
            # Enviar resultados
            result_data = {
                "counts": counts,
                "shots": shotsUsr,
                "provider": provider,
                "qb": qb,
                "users": users,
                "circuit_names": circuit_names
            }
            
            print(f"   ✅ Ejecución completada")
            
            # Enviar resultados al unscheduler para guardar en MongoDB
            print(f"   📤 Enviando resultados a unscheduler...")
            requests.post(self.unscheduler, json=result_data)
            print(f"   ✅ Resultados enviados correctamente")
            
        except Exception as e:
            print(f"   ❌ Error en ejecución: {e}")
        
        end_time = time.process_time()
        elapsed_time = end_time - start_time
        
        print(f"\n⏱️  TIEMPO TOTAL: {elapsed_time:.6f} segundos")
        
        # Guardar métricas
        with open("./SalidaCompressedML.txt", 'a', encoding='utf-8') as file:
            file.write(f"\n{'='*60}\n")
            file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            file.write(f"Provider: {provider}\n")
            file.write(f"Circuitos comprimidos: {len(compressed_data)}\n")
            file.write(f"Circuitos seleccionados: {len(seleccionados)}\n")
            file.write(f"Qubits totales (comprimidos): {selected_qubits}/{max_qubits}\n")
            file.write(f"Utilización: {selected_qubits/max_qubits*100:.1f}%\n")
            file.write(f"Tiempo total: {elapsed_time:.6f} seg\n")
            file.write(f"Detalles compresión:\n")
            for item in selected_compressed:
                file.write(f"  - {item['circuit_name']}: {item['original_qubits']} → {item['qubits_compressed']} qubits\n")
        
        # Gestión del temporizador
        if not queue:
            print(f"✅ Cola vacía, deteniendo temporizador.")
            self.services['Compressed_ML_Knapsack'].timers[provider].stop()
        else:
            print(f"🔁 Reiniciando temporizador ({len(queue)} circuitos restantes)...")
            self.services['Compressed_ML_Knapsack'].timers[provider].reset()
        
        print("🔶"*40 + "\n")

    

    def get_ibm_machine(self) -> str:
        """
        Returns the IBM machine of the scheduler

        Returns:
            str: The IBM machine of the scheduler
        """
        return self.machine_ibm
    
    def get_ibm(self):
        return self.executeCircuitIBM
    