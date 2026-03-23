from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
import numpy as np

qreg_q = QuantumRegister(9, 'q')
creg_c = ClassicalRegister(8, 'c')
circuit = QuantumCircuit(qreg_q, creg_c)

# Preparación masiva
for i in range(8):
    circuit.h(qreg_q[i])
circuit.x(qreg_q[8])

# Cascada de rotaciones controladas densas (Estilo QFT)
for i in range(8):
    for j in range(i + 1, 8):
        angulo = -np.pi / (2 ** (j - i))
        circuit.cp(angulo, qreg_q[j], qreg_q[i])

# Capa de Swaps
circuit.swap(qreg_q[0], qreg_q[7])
circuit.swap(qreg_q[1], qreg_q[6])
circuit.swap(qreg_q[2], qreg_q[5])
circuit.swap(qreg_q[3], qreg_q[4])

circuit.barrier(qreg_q)

# Medida monolítica final (Kriptonita del compresor)
for i in range(8):
    circuit.measure(qreg_q[i], creg_c[i])