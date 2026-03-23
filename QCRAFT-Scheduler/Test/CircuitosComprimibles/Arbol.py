from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit

# 7 Qubits lógicos: 4 base, 2 intermedios, 1 final
qreg_q = QuantumRegister(7, 'q')
creg_c = ClassicalRegister(7, 'c')
circuit = QuantumCircuit(qreg_q, creg_c)

# --- FASE 1: Las 4 Hojas base (Qubits 0 al 3) ---
circuit.h([qreg_q[0], qreg_q[1], qreg_q[2], qreg_q[3]])

# Interactúan y guardan el resultado en los nodos intermedios (4 y 5)
circuit.ccx(qreg_q[0], qreg_q[1], qreg_q[4]) # AND cuántico
circuit.ccx(qreg_q[2], qreg_q[3], qreg_q[5]) # AND cuántico

# Las hojas mueren
circuit.measure([qreg_q[0], qreg_q[1], qreg_q[2], qreg_q[3]], 
                [creg_c[0], creg_c[1], creg_c[2], creg_c[3]])

circuit.barrier()

# --- FASE 2: Nodos Intermedios (Qubits 4 y 5) ---
# Interactúan y guardan en el nodo final (6)
circuit.ccx(qreg_q[4], qreg_q[5], qreg_q[6])

# Los nodos intermedios mueren
circuit.measure([qreg_q[4], qreg_q[5]], [creg_c[4], creg_c[5]])

circuit.barrier()

# --- FASE 3: Nodo Final (Qubit 6) ---
circuit.h(qreg_q[6])
circuit.measure(qreg_q[6], creg_c[6])