# Guía de la Política de Compresión de Topología

## 📋 Descripción

La política `Topology_Compressed` aplica compresión de topología a los circuitos cuánticos antes de su ejecución, permitiendo reducir significativamente el número de qubits físicos necesarios.

## 🎯 Funcionamiento

### 1. **Selección de Circuitos**
   - Agrupa circuitos de la cola hasta llenar la capacidad máxima de qubits
   - Similar a otras políticas, pero preparado para compresión posterior

### 2. **Compresión mediante AdvancedTopologyCompressor**
   El compresor realiza tres pasos principales:
   
   a) **Adelanto de Medidas** (`_advance_measurements`)
      - Identifica qubits que sólo se miden sin operaciones adicionales
      - Mueve las medidas lo más temprano posible en el circuito
      
   b) **Cálculo de Tiempos de Vida** (`_get_qubit_lifetimes`)
      - Determina cuándo cada qubit lógico está activo
      - Identifica oportunidades de reutilización
      
   c) **Reutilización de Qubits Físicos** (`_rebuild_circuit`)
      - Mapea múltiples qubits lógicos a menos qubits físicos
      - Ejemplo: 20 qubits lógicos → 10 qubits físicos
      - Añade operaciones RESET cuando se reutiliza un qubit

### 3. **Adaptación a Topología Física**
   - Si hay `coupling_map` disponible, aplica routing a la arquitectura real
   - Usa `transpile` de Qiskit con método SABRE
   - Minimiza SWAP gates necesarios

### 4. **Ejecución**
   - Ejecuta el circuito comprimido en el backend
   - Mantiene el mapeo de qubits original (`qb`) para el unscheduler
   - Permite descomponer correctamente los resultados

## 🔧 Uso

### Endpoint HTTP
```http
POST http://{HOST}:{PORT}/service/Topology_Compressed
Content-Type: application/json

{
    "circuit": "...",
    "num_qubits": 15,
    "shots": 1024,
    "user": "user123",
    "circuit_name": "bell_state",
    "maxDepth": 10,
    "provider": "ibm",
    "Iteracion": 0
}
```

### Desde Python
```python
import requests

data = {
    "circuit": "OPENQASM 2.0;...",
    "num_qubits": 15,
    "shots": 1024,
    "user": "user_id",
    "circuit_name": "my_circuit",
    "maxDepth": 10,
    "provider": "ibm",  # o "aws"
    "Iteracion": 0
}

response = requests.post(
    "http://localhost:5000/service/Topology_Compressed",
    json=data
)
```

## 📊 Logs y Métricas

### Console Logs
La política genera logs detallados en consola:

```
🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷
🎯 POLÍTICA: TOPOLOGY COMPRESSED (IBM)
🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷🔷

📊 ESTADO INICIAL DE LA COLA:
   Circuitos en espera: 5
   Capacidad máxima: 127 qubits
   Total qubits en cola: 75

🔍 SELECCIONANDO CIRCUITOS PARA BATCH...
   ✓ Añadido: bell_state (15 qubits) → Total: 15/127
   ✓ Añadido: ghz_state (20 qubits) → Total: 35/127

✅ BATCH SELECCIONADO:
   Circuitos a ejecutar: 2
   Qubits lógicos totales: 35
   Utilización: 27.6%
```

### Logs de Compresión
```
================================================================================
🔄 INICIANDO EJECUCIÓN CON COMPRESIÓN DE TOPOLOGÍA
📊 Provider: IBM | Machine: ibm_fez
================================================================================

🔢 Circuito original: 35 qubits lógicos totales
   📦 Compuesto por 2 circuitos individuales: [15, 20]

🗜️ APLICANDO COMPRESIÓN DE TOPOLOGÍA...
   Compresor: IBM

✨ COMPRESIÓN COMPLETADA:
   📉 Qubits: 35 → 18
   💾 Ahorro: 48.6%
   📏 Depth comprimido: 25
   🚪 Gates comprimidos: 87

🚀 EJECUTANDO CIRCUITO EN IBM...
   Machine: ibm_fez
   Shots: 1024

✅ EJECUCIÓN COMPLETADA
   Resultados obtenidos: 128 estados medidos
```

### Archivo de Métricas
Se genera el archivo `SalidaTopologyCompressed.txt` con:
```
============================================================
Timestamp: 2026-02-23 14:30:45
Provider: ibm
Circuitos procesados: 2
Qubits lógicos totales: 35
Qubits por circuito: [15, 20]
Tiempo de scheduling: 0.023456 seg
Circuitos en cola restante: 3
```

## 🔍 Ventajas

1. **Reducción de Recursos**: 
   - Circuitos de 20+ qubits pueden reducirse a ~10 qubits físicos
   - Ahorro típico: 30-50%

2. **Mayor Acceso**:
   - Permite ejecutar circuitos en backends con menos qubits disponibles
   - Reduce tiempos de cola en backends populares

3. **Mejor Fidelidad**:
   - Menos qubits físicos = menos ruido cuántico
   - Circuitos más compactos tienen mejor coherencia

4. **Adaptación a Topología**:
   - Mapeo óptimo según arquitectura del chip
   - Minimiza SWAPs necesarios

## ⚠️ Consideraciones

1. **Overhead de RESET**:
   - Se añaden operaciones RESET al reutilizar qubits
   - Puede aumentar ligeramente el tiempo de ejecución

2. **Depth del Circuito**:
   - La reutilización puede aumentar el depth total
   - Trade-off entre ancho y profundidad

3. **Compatibilidad**:
   - El backend debe soportar operaciones RESET
   - No todos los simuladores/hardware lo soportan igual

4. **Resultados**:
   - Los resultados se descomponen correctamente usando `qb` original
   - El unscheduler no necesita cambios

## 🛠️ Configuración

### Topología del Backend
La topología se carga automáticamente:
- **IBM**: Lee del servicio `QiskitRuntimeService`
- **AWS**: Lee de AWS Braket Provider

Si falla la carga, usa compresor sin topología específica (sigue funcionando).

### Ajustes en scheduler_policies.py
```python
# Cambiar máquina IBM
self.machine_ibm = 'ibm_kyoto'  # o 'ibm_fez', 'local', etc.

# Cambiar máquina AWS
self.machine_aws = 'arn:aws:braket:...'  # o 'local'

# Cambiar límite de tiempo
self.time_limit_seconds = 15  # segundos
```

## 📈 Casos de Uso Ideales

1. **Circuitos con Medidas Intermedias**:
   - Simon's Algorithm
   - Quantum Teleportation
   - Algoritmos con medidas y resets

2. **Múltiples Circuitos Pequeños**:
   - Batches de circuitos independientes
   - Variational algorithms con múltiples evaluaciones

3. **Backends Limitados**:
   - Cuando el backend tiene menos qubits que el circuito necesita
   - Para reducir tiempos de espera en cola

## 🔗 Archivos Relacionados

- `circuit_Compresor.py`: Implementación del compresor
- `Ibm_api.py`: Obtención de topología IBM
- `Aws_api.py`: Obtención de topología AWS
- `scheduler_policies.py`: Integración en el scheduler

## 📚 Referencias

- [Qiskit Circuit Optimization](https://qiskit.org/documentation/stubs/qiskit.transpiler.passes.html)
- [SABRE Routing](https://arxiv.org/abs/1809.02573)
- [Dynamic Circuit Compression Techniques](https://arxiv.org/abs/2008.10817)
