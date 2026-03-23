#!/usr/bin/env python
# coding: utf-8

# import libraries
from qiskit import transpile
import qiskit.providers
from qiskit_ibm_runtime import SamplerV2 as Sampler, QiskitRuntimeService
from qiskit import QuantumCircuit
from qiskit.circuit.library import MCXGate
from qiskit_aer import AerSimulator
import json
import os
import qiskit
import numpy as np
import re
import threading
import config

class executeCircuitIBM:
    def __init__(self):
        self.transpile_lock = threading.Lock()
        self.condition = threading.Condition()
        
        self.service = QiskitRuntimeService(channel='ibm_cloud'  ,                                 token="",
                                   instance="")
        
        all_jobs = self.service.jobs()
        
        self.queued_jobs = len([job for job in all_jobs if job.status() == qiskit.providers.JobStatus.QUEUED])  # Number of queued jobs de la cola generado


    def load_account_ibm(self) -> QiskitRuntimeService:
        """
        Loads the IBM Quantum account.

        Returns:
            QiskitRuntimeService: The service with the IBM Quantum account loaded.
        """
        # Load your IBM Quantum account
        return self.service

    def obtain_machine(self, service:QiskitRuntimeService ,machine:str) -> qiskit.providers.BackendV2:
        """
        Obtains the information of the machine.

        Args:
            QiskitRuntimeService: The service to obtain the machine.        
            machine (str): The machine to obtain the information.

        Returns:
            qiskit.providers.BackendV2: The IBM backend.
        """
        # Load your IBM Quantum account
        backend = service.backend(machine)
        return backend


    def code_to_circuit_ibm(self, code_str:str) -> qiskit.QuantumCircuit:
        """
        Transforms a string representation of a circuit into a Qiskit circuit.
        Can handle both complete code (with register definitions) and incomplete code (only operations).

        Args:
            code_str (str): The string representation of the Qiskit circuit.

        Returns:
            qiskit.QuantumCircuit: The circuit object.
        """
        try:
            lines = code_str.strip().split('\n')
            qreg = creg = circuit = None
            qreg_name = "qreg_q"
            creg_name = "creg_c"
            
            # First pass: try to find register definitions and determine max indices
            max_qubit_index = -1
            max_cbit_index = -1
            has_qreg_def = False
            has_creg_def = False
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#') or 'import' in line or line.startswith('from'):
                    continue
                
                if "QuantumRegister" in line:
                    has_qreg_def = True
                if "ClassicalRegister" in line:
                    has_creg_def = True
                
                # Find max qubit and cbit indices from operations
                if "qreg_q[" in line or "_q[" in line or "circuit." in line:
                    # Extract qubit indices
                    qubit_matches = re.findall(r'qreg_q\[(\d+)\]|_q\[(\d+)\]', line)
                    for match in qubit_matches:
                        idx = int(match[0] if match[0] else match[1])
                        max_qubit_index = max(max_qubit_index, idx)
                
                if "creg_c[" in line or "_c[" in line:
                    # Extract clbit indices
                    cbit_matches = re.findall(r'creg_c\[(\d+)\]|_c\[(\d+)\]', line)
                    for match in cbit_matches:
                        idx = int(match[0] if match[0] else match[1])
                        max_cbit_index = max(max_cbit_index, idx)
            
            # If no register definitions found, create them based on max indices
            if not has_qreg_def and max_qubit_index >= 0:
                num_qubits = max_qubit_index + 1
                qreg = qiskit.QuantumRegister(num_qubits, 'q')
                print(f"        ℹ️  Auto-creando QuantumRegister con {num_qubits} qubits")
            
            if not has_creg_def and max_cbit_index >= 0:
                num_clbits = max_cbit_index + 1
                creg = qiskit.ClassicalRegister(num_clbits, 'c')
                print(f"        ℹ️  Auto-creando ClassicalRegister con {num_clbits} clbits")
            
            # Second pass: process lines for actual parsing
            for line in lines:
                line = line.strip()
                
                if not line or line.startswith('#') or 'import' in line or line.startswith('from'):
                    continue
                    
                if any(skip in line for skip in ['gate_machines_arn', 'shots =', 'provider =', 'backend =', 
                                                   'transpile(', 'execute(', 'job =', 'job_result =', 
                                                   'print(', 'get_counts(', 'Aer.', 'IBMProvider']):
                    continue
                
                try:
                    if "QuantumRegister" in line and not qreg:
                        qreg_name = line.split('=')[0].strip()
                        num_qubits_str = line.split('(')[1].split(')')[0].split(',')[0].strip()
                        num_qubits = int(num_qubits_str)
                        qreg = qiskit.QuantumRegister(num_qubits, 'q')
                        
                    elif "ClassicalRegister" in line and not creg:
                        creg_name = line.split('=')[0].strip()
                        num_clbits_str = line.split('(')[1].split(')')[0].split(',')[0].strip()
                        num_clbits = int(num_clbits_str)
                        creg = qiskit.ClassicalRegister(num_clbits, 'c')
                        
                    elif "QuantumCircuit" in line and "=" in line and not circuit:
                        if qreg is not None:
                            if creg is not None:
                                circuit = qiskit.QuantumCircuit(qreg, creg)
                            else:
                                circuit = qiskit.QuantumCircuit(qreg)
                    
                    # If we found operations but no circuit yet, create it now
                    elif "circuit." in line and circuit is None and qreg is not None:
                        if creg is not None:
                            circuit = qiskit.QuantumCircuit(qreg, creg)
                        else:
                            circuit = qiskit.QuantumCircuit(qreg)
                        
                    elif "circuit." in line and circuit is not None:
                        # Procesar operaciones del circuito
                        if ".c_if(" in line:
                            operation, condition = line.split('.c_if(')
                        else:
                            operation = line
                            condition = None
                        
                        # Parse gate operations
                        gate_name = operation.split('circuit.')[1].split('(')[0]
                        
                        # Extraer argumentos
                        try:
                            args_str = operation.split('(', 1)[1].rsplit(')', 1)[0].strip()
                            if args_str:
                                args = re.split(r'\s*,\s*', args_str)
                            else:
                                args = ['']
                        except:
                            continue
                        
                        if gate_name == "measure":
                            if len(args) >= 2 and '[' in args[0] and '[' in args[1]:
                                try:
                                    qubit_idx = int(args[0].split('[')[1].strip(']').split('+')[0])
                                    cbit_idx = int(args[1].split('[')[1].strip(']').split('+')[0])
                                    if qreg and creg:
                                        circuit.measure(qreg[qubit_idx], creg[cbit_idx])
                                except:
                                    pass
                                    
                        elif gate_name == "barrier":
                            if not args[0] or args[0] == '':
                                circuit.barrier()
                            elif args[0] == qreg_name:
                                circuit.barrier(*qreg)
                            else:
                                # Barrier con qubits específicos
                                try:
                                    qubit_indices = []
                                    for arg in args:
                                        if '[' in arg:
                                            idx = int(arg.split('[')[1].strip(']').split('+')[0])
                                            qubit_indices.append(qreg[idx])
                                    if qubit_indices:
                                        circuit.barrier(*qubit_indices)
                                except:
                                    pass
                                    
                        elif gate_name == "append":
                            # Manejar gates multi-control
                            try:
                                gate_type = args[0]
                                qubits = [qreg[int(re.search(r'\[(\d+)\]', arg).group(1))] for arg in args[1:] if '[' in arg]
                                control_qubits = qubits[:-1]
                                target_qubit = qubits[-1]
                                if gate_type == 'mc_x_gate':
                                    mcx = MCXGate(len(control_qubits))
                                    circuit.append(mcx, control_qubits + [target_qubit])
                                elif gate_type == 'mc_y_gate':
                                    circuit.sdg(target_qubit)
                                    mcx = MCXGate(len(control_qubits))
                                    circuit.append(mcx, control_qubits + [target_qubit])
                                    circuit.s(target_qubit)
                                elif gate_type == 'mc_z_gate':
                                    circuit.h(target_qubit)
                                    mcx = MCXGate(len(control_qubits))
                                    circuit.append(mcx, control_qubits + [target_qubit])
                                    circuit.h(target_qubit)
                            except:
                                pass
                        else:
                            # Gate normal (h, x, cx, cp, etc.)
                            try:
                                # Extraer qubits (argumentos que contienen '[')
                                qubit_args = [arg for arg in args if '[' in arg]
                                qubits = []
                                for arg in qubit_args:
                                    idx = int(arg.split('[')[1].strip(']').split('+')[0])
                                    qubits.append(qreg[idx])
                                
                                # Extraer parámetros (argumentos sin '[')
                                param_args = [arg for arg in args if '[' not in arg and arg.strip()]
                                params = []
                                for param_str in param_args:
                                    try:
                                        # Evaluar parámetros (ej: np.pi / 2)
                                        param = eval(param_str, {"__builtins__": None, "np": np, "pi": np.pi}, {})
                                        params.append(param)
                                    except:
                                        pass
                                
                                # Aplicar gate
                                if hasattr(circuit, gate_name):
                                    gate_func = getattr(circuit, gate_name)
                                    if params:
                                        gate_operation = gate_func(*params, *qubits)
                                    else:
                                        gate_operation = gate_func(*qubits)
                                    
                                    if condition:
                                        creg_name, val = condition.split(')')[0].split(',')
                                        val = int(val.strip())
                                        gate_operation.c_if(creg, val)
                            except Exception as gate_error:
                                # Silenciosamente saltar gates que no se puedan parsear
                                pass
                                
                except Exception as line_error:
                    # Si una línea falla, continuar con la siguiente
                    continue
                    
        except Exception as e:
            raise ValueError(f"Invalid circuit code: {str(e)}")

        if circuit is None:
            raise ValueError("No valid QuantumCircuit found in code")
        
        return circuit


    def get_transpiled_circuit_depth_ibm(self, circuit:QuantumCircuit, backend:qiskit.providers.BackendV2) -> int:
        """
        Transpiles a circuit and returns its depth.

        Args:
            circuit (QuantumCircuit): The circuit to transpile.        
            backend (qiskit.providers.BackendV2): The machine to transpile the circuit

        Returns:
            int: The depth of the transpiled circuit.
        """
        # Load your IBM Quantum account
        with self.transpile_lock:
            qc_basis = transpile(circuit, backend=backend)

        return qc_basis.depth()


    # Ejecutar el circuito
    def runIBM(self, machine:str, circuit:QuantumCircuit, shots:int) -> dict:
        """
        Executes a circuit in the IBM cloud.

        Args:
            machine (str): The machine to execute the circuit.        
            circuit (QuantumCircuit): The circuit to execute.        
            shots (int): The number of shots to execute the circuit.

        Returns:
            dict: The results of the circuit execution.
        """

        if machine == "local":
            backend = AerSimulator()
            x = int(shots)
            job = backend.run(circuit, shots=x)
            result = job.result()
            counts = result.get_counts()
            return counts
        else:
            # Load your IBM Quantum account

            service = self.service
            backend = service.backend(machine)
            qc_basis = transpile(circuit, backend=backend)
            x = int(shots)
            job = backend.run(qc_basis, shots=x) 
            result = job.result()
            counts = result.get_counts()
            return counts

    def retrieve_result_ibm(self, id) -> dict:
        """
        Retrieves the results of a circuit execution in the IBM cloud.

        Args:
            id (str): The id of the job to retrieve the results from.

        Returns:
            dict: The results of the task execution.
        """
        # Load your IBM Quantum account
        service = self.service
        job = service.job(id)
        result = job.result()
        # Get the classical register name from the circuit metadata
        creg_name = list(result[0].data._fields.keys())[0] if hasattr(result[0].data, '_fields') else 'c'
        counts = getattr(result[0].data, creg_name).get_counts()
        return counts

    def runIBM_save(self, machine:str, circuit:QuantumCircuit, shots:int,users:list, qubit_number:list, circuit_names:list) -> dict:
        """
        Executes a circuit in the IBM cloud and saves the task id if the machine crashes.

        Args:
            machine (str): The machine to execute the circuit.        
            circuit (QuantumCircuit): The circuit to execute.        
            shots (int): The number of shots to execute the circuit.        
            users (list): The users that executed the circuit.        
            qubit_number (list): The number of qubits of the circuit per user.        
            circuit_names (list): The name of the circuit that was executed per user.

        Returns:
            dict: The results of the circuit execution.
        """

        if machine == "local":
            backend = AerSimulator()
            x = int(shots)
            job = backend.run(circuit, shots=x)
            result = job.result()
            counts = result.get_counts()
            return counts
        else:
            # Load your IBM Quantum account

            service = self.service
            backend = service.backend(machine)
            sampler = Sampler(mode=backend)
            #sampler.options.execution.rep_delay = 0.5 # set it to the maximum of the machine instead -> config.rep_delay_range[1]
            with self.transpile_lock:
                qc_basis = transpile(circuit, backend=backend)
            x = int(shots)

            while True:
                with self.condition:   
                    if self.queued_jobs < 3:
                        self.queued_jobs += 1
                        job = sampler.run([qc_basis], shots=x)
                        break
                    else:
                        self.condition.wait()


            # -----------------------------------------------------#
            id = job.job_id() # Get the job id
            provider = 'ibm'
            user_shots = [shots] * len(circuit_names)
            script_dir = os.path.dirname(os.path.realpath(__file__))
            ids_file = os.path.join(script_dir, 'ids.txt')  # create the path to the results file in the script's directory
            with open(ids_file, 'a') as file:
                file.write(json.dumps({id:(users,qubit_number, user_shots, provider, circuit_names)}))
                file.write('\n')
            # Write the id in a file, along with the users, and their qubit numbers
            # -----------------------------------------------------#

            result = job.result()
            # Get the classical register name dynamically
            creg_name = list(result[0].data._fields.keys())[0] if hasattr(result[0].data, '_fields') else 'c'
            counts = getattr(result[0].data, creg_name).get_counts()

            with self.condition:
                self.queued_jobs -= 1
                self.condition.notify()

            # -----------------------------------------------------#

            #Seach for the id in the file and delete the line
            with open(ids_file, 'r') as file:
                lines = file.readlines()
            with open(ids_file, 'w') as file:
                for line in lines:
                    line_dict = json.loads(line.strip())
                    if list(line_dict.keys())[0] != id:
                        file.write(line)

            # -----------------------------------------------------#

            return counts
