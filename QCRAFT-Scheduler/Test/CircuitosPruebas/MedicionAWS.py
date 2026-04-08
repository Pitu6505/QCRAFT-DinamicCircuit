import numpy as np
from braket.devices import LocalSimulator
from braket.circuits import Circuit
from collections import Counter

shots = 128

# Inicializamos el circuito
circuit = Circuit()

# 1. Aplicamos Hadamard para poner el qubit 0 en superposición (50% |0>, 50% |1>)
circuit.h(0)

# 2. PRIMERA MEDICIÓN en el qubit 0 (Mid-Circuit Measurement)
circuit.measure(0)

# 3. Aplicamos una compuerta X (NOT) al mismo qubit.
# Dado que el qubit ya colapsó a 0 o 1 en el paso anterior, esta compuerta 
# invertirá ese valor clásico (si colapsó en 0 pasará a 1, y viceversa).
circuit.x(0)

# 4. SEGUNDA MEDICIÓN en el mismo qubit 0
circuit.measure(0)

# --- Ejecución en el simulador local ---
device = LocalSimulator()

# Ejecutamos el circuito
task = device.run(circuit, shots=shots)
result = task.result()

# Mostramos el circuito por consola para visualizarlo
print("Circuito:")
print(circuit)
print("\n")

# Obtenemos y mostramos el conteo de las mediciones
counts = result.measurement_counts
print("Resultados de las mediciones (Primera medición, Segunda medición):")
print(counts)