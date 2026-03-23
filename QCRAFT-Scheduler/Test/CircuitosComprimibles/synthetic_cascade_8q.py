from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
import numpy as np

qreg_q = QuantumRegister(8, 'q')
creg_c = ClassicalRegister(8, 'c')
circuit = QuantumCircuit(qreg_q, creg_c)

# --- BLOQUE A (Qubits 0 al 3) ---
circuit.h(qreg_q[0])
circuit.h(qreg_q[1])
circuit.cx(qreg_q[0], qreg_q[2])
circuit.cp(np.pi/2, qreg_q[1], qreg_q[3])
circuit.swap(qreg_q[2], qreg_q[3])

# ¡Medida temprana! Los qubits 0, 1 y 2 mueren aquí.
circuit.barrier(qreg_q[0], qreg_q[1], qreg_q[2])
circuit.measure(qreg_q[0], creg_c[0])
circuit.measure(qreg_q[1], creg_c[1])
circuit.measure(qreg_q[2], creg_c[2])

# --- BLOQUE B (Qubits 4 al 7) ---
# Empiezan a trabajar ahora. El compresor reciclará los cables físicos 0, 1 y 2.
circuit.h(qreg_q[4])
circuit.cx(qreg_q[3], qreg_q[4]) # Q3 sobrevive para transferir información

circuit.measure(qreg_q[3], creg_c[3]) # Q3 muere ahora

circuit.h(qreg_q[5])
circuit.cp(np.pi/4, qreg_q[4], qreg_q[6])
circuit.cp(np.pi/8, qreg_q[5], qreg_q[7])
circuit.swap(qreg_q[6], qreg_q[7])

circuit.barrier(qreg_q[4], qreg_q[5], qreg_q[6], qreg_q[7])
circuit.measure(qreg_q[4], creg_c[4])
circuit.measure(qreg_q[5], creg_c[5])
circuit.measure(qreg_q[6], creg_c[6])
circuit.measure(qreg_q[7], creg_c[7])