# 🔍 Análisis: Política Topology_Compressed - Cuándo se Comprime

## 📋 Implementación Actual

### Flujo de Ejecución Paso a Paso

```
1️⃣ send_topology_compressed()
   ├─ Lee cola de circuitos pendientes
   ├─ Selecciona circuitos basándose en QUBITS ORIGINALES (sin comprimir)
   ├─ Límite: sum(qubits_originales) <= max_qubits (127)
   └─ Ejemplo: Acepta [20, 30, 40, 35] = 125 qubits ✓
   
2️⃣ create_circuit()
   ├─ Compone los circuitos seleccionados en uno solo
   └─ Genera código con 125 qubits lógicos
   
3️⃣ executeCircuitCompressed() ← AQUÍ SE COMPRIME
   ├─ Convierte código → QuantumCircuit (125 qubits)
   ├─ 🗜️ APLICA COMPRESIÓN: 125 qubits → ~63 qubits
   ├─ Mapea a topología física del backend
   └─ Ejecuta circuito de 63 qubits
```

### Característica Clave

**La compresión se aplica DESPUÉS de seleccionar y componer los circuitos.**

Esto significa:
- ✅ La selección usa límites conservadores (no sobrepasa capacidad física)
- ✅ La compresión reduce el circuito final antes de ejecutar
- ❌ No aprovecha la compresión para meter MÁS circuitos en el batch

---

## 🎯 ¿Qué Tipo de Política Es?

### Política Actual: **TIEMPO con Compresión Post-Composición**

```python
# Comportamiento similar a la política 'time'
if num_qubits + sumQb <= max_qubits:  # Límite basado en qubits originales
    urls.append(item)
```

**Ventajas:**
- ✅ Seguro: nunca sobrepasa la capacidad del backend
- ✅ Simple: no necesita estimar ratios de compresión
- ✅ Conservador: funciona aunque la compresión falle

**Desventajas:**
- ❌ Subutiliza la capacidad: podría meter más circuitos si supiera que van a comprimirse
- ❌ No optimiza basándose en el ahorro esperado

---

## 🔄 Alternativas Propuestas

### Opción A: **Política Agresiva con Factor de Compresión**

```python
def send_topology_compressed_aggressive(self, queue, max_qubits, ...):
    COMPRESSION_FACTOR = 0.6  # Asumimos que comprimirá a ~60% del original
    effective_capacity = max_qubits / COMPRESSION_FACTOR  # 127 / 0.6 = ~211 qubits
    
    # Aceptar más circuitos basándose en capacidad efectiva
    if num_qubits + sumQb <= effective_capacity:
        urls.append(item)
    
    # Luego comprimir y verificar
    compressed_qubits = compress(circuit).num_qubits
    if compressed_qubits > max_qubits:
        # Manejar caso de sobrepaso (descartando o reintentando)
```

**Ventajas:**
- ✅ Aprovecha la compresión para procesar más circuitos por batch
- ✅ Mayor throughput potencial

**Desventajas:**
- ❌ Riesgoso: podría fallar si la compresión no es suficiente
- ❌ Necesita mecanismo de fallback

---

### Opción B: **Política con Pre-Compresión Individual**

```python
def send_topology_compressed_smart(self, queue, max_qubits, ...):
    compressed_estimates = []
    
    # Pre-calcular cuánto comprimiría cada circuito individualmente
    for circuit in queue:
        qc = parse_circuit(circuit)
        compressed = compressor.compress_and_map(qc)
        estimated_size = compressed.num_qubits
        compressed_estimates.append((circuit, estimated_size))
    
    # Seleccionar basándose en tamaños comprimidos estimados
    sumCompressed = 0
    for circuit, compressed_size in compressed_estimates:
        if compressed_size + sumCompressed <= max_qubits:
            selected.append(circuit)
```

**Ventajas:**
- ✅ Información precisa sobre tamaño real comprimido
- ✅ Optimización basada en datos reales

**Desventajas:**
- ❌ Muy costoso computacionalmente (comprime 2 veces)
- ❌ La compresión individual ≠ compresión del batch compuesto
- ❌ Overhead significativo

---

### Opción C: **Política Adaptativa (Recomendada)**

```python
def send_topology_compressed_adaptive(self, queue, max_qubits, ...):
    # Mantener historial de ratios de compresión
    if not hasattr(self, 'compression_history'):
        self.compression_history = []
    
    # Calcular ratio promedio de ejecuciones previas
    if len(self.compression_history) > 0:
        avg_ratio = sum(self.compression_history) / len(self.compression_history)
        safety_margin = 1.1  # 10% de margen de seguridad
        effective_capacity = max_qubits / (avg_ratio * safety_margin)
    else:
        effective_capacity = max_qubits  # Primera vez: conservador
    
    # Seleccionar circuitos
    for circuit in queue:
        if num_qubits + sumQb <= effective_capacity:
            selected.append(circuit)
    
    # Después de comprimir, guardar el ratio real
    compression_ratio = compressed_qubits / original_qubits
    self.compression_history.append(compression_ratio)
    
    # Mantener solo últimos 10 valores
    if len(self.compression_history) > 10:
        self.compression_history.pop(0)
```

**Ventajas:**
- ✅ Aprende del historial de ejecuciones
- ✅ Se adapta a diferentes tipos de circuitos
- ✅ Incluye margen de seguridad
- ✅ Bajo overhead computacional

**Desventajas:**
- ❌ Empieza conservador hasta tener datos
- ⚠️ Puede necesitar ajuste si cambian drásticamente los tipos de circuitos

---

## 🎯 Recomendación

### Para Producción: **Opción C (Adaptativa)**

Implementar la política adaptativa que:
1. Empieza conservadora (como ahora)
2. Aprende el ratio de compresión típico
3. Ajusta dinámicamente la capacidad efectiva
4. Incluye margen de seguridad para evitar fallos

### Para Testing: **Opción A (Agresiva con factor fijo)**

Usar un factor de compresión estimado (ej: 0.6) para ver cuánto más throughput se consigue.

---

## 📊 Comparación de Throughput Estimado

Asumiendo compresión típica del 40% (0.6 del original):

| Política | Qubits Aceptados | Qubits Ejecutados | Circuitos/Batch | Throughput Relativo |
|----------|------------------|-------------------|-----------------|---------------------|
| **Actual (Conservadora)** | 127 | ~76 | ~4-6 | 1.0x (baseline) |
| **Agresiva (factor 0.6)** | ~211 | ~126 | ~8-10 | 1.7x |
| **Adaptativa (aprendizaje)** | 127→190 | 76→114 | 4→7 | 1.0x→1.5x |

---

## ✅ Implementación Recomendada

¿Quieres que implemente la **Opción C (Adaptativa)**?

Requiere:
1. Añadir `self.compression_history` al `__init__`
2. Modificar la selección en `send_topology_compressed`
3. Guardar ratios después de cada compresión
4. Logs adicionales mostrando la capacidad efectiva usada

---

## 🔧 Para Usar la Actual (Ya Implementada)

```bash
# Ejecutar el test
cd C:\Users\Usuario\Desktop\Investigacion\QCRAFT-DinamicCircuit\QCRAFT-Scheduler\Test
python scheduler-async-compressed.py
```

¿Qué versión prefieres implementar?
