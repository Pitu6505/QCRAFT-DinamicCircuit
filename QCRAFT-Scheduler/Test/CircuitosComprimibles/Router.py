from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
import numpy as np

qreg_q = QuantumRegister(8, 'q')
creg_c = ClassicalRegister(8, 'c')
circuit = QuantumCircuit(qreg_q, creg_c)

# Qubit 0 es el Router Central (siempre vivo)
circuit.h(qreg_q[0])

# Los periféricos (clientes) entran uno a uno
for i in range(1, 8):
    # Nace el cliente 'i'
    circuit.h(qreg_q[i])
    circuit.cp(np.pi / i, qreg_q[i], qreg_q[0])
    circuit.cx(qreg_q[i], qreg_q[0])
    
    # El cliente 'i' muere tras dar su información al centro
    circuit.measure(qreg_q[i], creg_c[i])

# El Router Central se mide al final de toda la ejecución
circuit.measure(qreg_q[0], creg_c[0])