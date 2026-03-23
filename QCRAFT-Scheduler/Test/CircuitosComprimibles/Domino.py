from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
import numpy as np

# Circuito Lógico de 10 qubits
qreg_q = QuantumRegister(10, 'q')
creg_c = ClassicalRegister(10, 'c')
circuit = QuantumCircuit(qreg_q, creg_c)

# Iniciamos la "ficha de dominó"
circuit.h(qreg_q[0])

# Reacción en cadena
for i in range(9):
    # Preparamos el siguiente qubit
    circuit.rx(np.pi/4, qreg_q[i+1])
    
    # Entrelazamos el actual con el siguiente
    circuit.cx(qreg_q[i], qreg_q[i+1])
    
    # MEDIDA INMEDIATA: El qubit actual ya no se necesita, muere aquí.
    circuit.measure(qreg_q[i], creg_c[i])
    
    # Pequeña barrera visual (opcional)
    circuit.barrier(qreg_q[i], qreg_q[i+1])

# Medimos el último qubit que queda vivo al final
circuit.measure(qreg_q[9], creg_c[9])