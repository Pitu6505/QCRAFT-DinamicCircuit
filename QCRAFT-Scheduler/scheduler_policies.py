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
        self.max_qubits = 127
        self.forced_threshold = 12
        self.machine_ibm =  'ibm_fez' #''local'
        self.machine_aws = 'local' #'arn:aws:braket:::device/quantum-simulator/amazon/sv1'
        self.executeCircuitIBM = executeCircuitIBM()
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
                        'Optimizacion_PD': Policy(self.send_PD, self.max_qubits, self.time_limit_seconds , self.executeCircuit, self.machine_aws, self.machine_ibm),}
        
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
        if  n_qubits >= self.max_qubits and (service_name != 'Optimizacion_ML' and service_name != 'Optimizacion_PD'):
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
        for url, num_qubits, shots, user, circuit_name, depth, Iterator in urls:  #Aqui he cambiado algo
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
            code.insert(0, f"creg_c = ClassicalRegister({composition_qubits}, 'c')")  # Set composition_qubits as the number of classical bits
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

    

    def get_ibm_machine(self) -> str:
        """
        Returns the IBM machine of the scheduler

        Returns:
            str: The IBM machine of the scheduler
        """
        return self.machine_ibm
    
    def get_ibm(self):
        return self.executeCircuitIBM
    