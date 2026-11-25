import os
import hashlib
from typing import Optional, List
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Portal Escuela Candelaria Pérez v9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "escuela16")

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL no configurada en el servidor.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.strip().encode("utf-8")).hexdigest()

def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(150) NOT NULL,
            rut VARCHAR(30) UNIQUE,
            email VARCHAR(150),
            rol VARCHAR(30) NOT NULL,
            curso VARCHAR(100),
            asignatura VARCHAR(100),
            pie BOOLEAN DEFAULT FALSE,
            password_hash VARCHAR(255) NOT NULL,
            activo BOOLEAN DEFAULT TRUE,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cursos (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(80) UNIQUE NOT NULL,
            profesor_jefe VARCHAR(150)
        );
        CREATE TABLE IF NOT EXISTS calificaciones (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            asignatura VARCHAR(100) NOT NULL,
            evaluacion VARCHAR(100) NOT NULL,
            nota NUMERIC(3,1) NOT NULL,
            ponderacion INTEGER DEFAULT 100,
            fecha DATE DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS asistencia (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            curso VARCHAR(80) NOT NULL,
            fecha DATE DEFAULT CURRENT_DATE,
            estado VARCHAR(30) NOT NULL,
            UNIQUE(estudiante_id, fecha)
        );
        CREATE TABLE IF NOT EXISTS justificaciones (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            curso VARCHAR(80) NOT NULL,
            fecha_ausencia DATE NOT NULL,
            motivo TEXT NOT NULL,
            estado VARCHAR(30) DEFAULT 'Pendiente',
            respuesta_docente TEXT DEFAULT '',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS anotaciones (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            docente_nombre VARCHAR(150),
            tipo VARCHAR(30) NOT NULL,
            detalle TEXT NOT NULL,
            fecha DATE DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS entrevistas (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            docente_nombre VARCHAR(150),
            apoderado VARCHAR(150) NOT NULL,
            motivo TEXT NOT NULL,
            acuerdos TEXT NOT NULL,
            fecha DATE DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS alertas (
            id SERIAL PRIMARY KEY,
            titulo VARCHAR(150) NOT NULL,
            detalle TEXT NOT NULL,
            nivel VARCHAR(30) DEFAULT 'info',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Asegurar columnas nuevas si la tabla usuarios ya existía de v8.0
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS pie BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE calificaciones ADD COLUMN IF NOT EXISTS ponderacion INTEGER DEFAULT 100;")

    cur.execute("SELECT id FROM usuarios WHERE rol = 'maestro' LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO usuarios (nombre, rut, email, rol, password_hash) VALUES (%s, %s, %s, %s, %s)",
            ("Paulina Toledo", "directora", "direccion@escuelacandelariaperez.cl", "maestro", hash_pwd(ADMIN_PASSWORD))
        )
    conn.commit()
    cur.close()
    conn.close()

@app.on_event("startup")
def startup_event():
    try:
        init_db()
    except Exception as e:
        print("Error inicializando BD:", e)

class LoginRequest(BaseModel):
    identificador: str
    password: str
    tipo_portal: str

class UsuarioCreate(BaseModel):
    nombre: str
    rut: str
    email: Optional[str] = ""
    rol: str
    curso: Optional[str] = ""
    asignatura: Optional[str] = ""
    pie: Optional[bool] = False
    password: str

class NotaCreate(BaseModel):
    estudiante_id: int
    asignatura: str
    evaluacion: str
    nota: float
    ponderacion: int = 100

class AsistenciaItem(BaseModel):
    estudiante_id: int
    estado: str

class AsistenciaMasivaCreate(BaseModel):
    curso: str
    fecha: str
    registros: List[AsistenciaItem]

class JustificacionCreate(BaseModel):
    estudiante_id: int
    curso: str
    fecha_ausencia: str
    motivo: str

class JustificacionResolver(BaseModel):
    estado: str
    respuesta_docente: Optional[str] = ""

class AnotacionCreate(BaseModel):
    estudiante_id: int
    docente_nombre: str
    tipo: str
    detalle: str

class EntrevistaCreate(BaseModel):
    estudiante_id: int
    docente_nombre: str
    apoderado: str
    motivo: str
    acuerdos: str

class AlertaCreate(BaseModel):
    titulo: str
    detalle: str
    nivel: str = "info"

@app.get("/")
def health_check():
    return {"status": "ok", "version": "9.0", "app": "Escuela Candelaria Pérez - Decreto 67 & Convivencia"}

@app.post("/api/login")
def login(data: LoginRequest):
    conn = get_db()
    cur = conn.cursor()
    ident = data.identificador.strip()
    pwd_h = hash_pwd(data.password)

    if data.tipo_portal == "staff":
        cur.execute(
            """SELECT id, nombre, rut, email, rol, curso, asignatura, pie
               FROM usuarios
               WHERE (LOWER(nombre) = LOWER(%s) OR LOWER(rut) = LOWER(%s) OR LOWER(email) = LOWER(%s))
               AND rol IN ('maestro', 'docente') AND password_hash = %s AND activo = TRUE""",
            (ident, ident, ident, pwd_h)
        )
    else:
        cur.execute(
            """SELECT id, nombre, rut, email, rol, curso, asignatura, pie
               FROM usuarios
               WHERE (LOWER(rut) = LOWER(%s) OR LOWER(nombre) = LOWER(%s))
               AND rol = 'estudiante' AND password_hash = %s AND activo = TRUE""",
            (ident, ident, pwd_h)
        )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas o usuario no autorizado.")

    return {
        "ok": True,
        "usuario": {
            "id": user["id"],
            "nombre": user["nombre"],
            "rut": user["rut"],
            "email": user["email"],
            "rol": str(user["rol"]).strip().lower(),
            "curso": user["curso"] or "",
            "asignatura": user["asignatura"] or "",
            "pie": bool(user["pie"])
        }
    }

# --- ENDPOINTS MAESTRO / DIRECCIÓN ---
@app.get("/api/dashboard/maestro")
def dashboard_maestro():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, rut, email, rol, curso, asignatura, pie FROM usuarios ORDER BY rol, curso, nombre")
    usuarios = cur.fetchall()
    cur.execute("SELECT * FROM cursos ORDER BY nombre")
    cursos = cur.fetchall()
    cur.execute("SELECT * FROM alertas ORDER BY creado_en DESC LIMIT 20")
    alertas = cur.fetchall()
    cur.execute("SELECT ROUND(AVG(nota), 1) AS promedio FROM calificaciones")
    prom = cur.fetchone()["promedio"] or 0.0
    cur.execute("SELECT COUNT(*) AS pendientes FROM justificaciones WHERE estado = 'Pendiente'")
    just_pend = cur.fetchone()["pendientes"] or 0
    cur.close()
    conn.close()
    return {
        "usuarios": usuarios,
        "cursos": cursos,
        "alertas": alertas,
        "promedio_general": float(prom),
        "justificaciones_pendientes": just_pend
    }

@app.post("/api/usuarios")
def crear_usuario(u: UsuarioCreate):
    conn = get_db()
    cur = conn.cursor()
    rol_clean = u.rol.strip().lower()
    if rol_clean not in ("maestro", "docente", "estudiante"):
        raise HTTPException(status_code=400, detail="Rol inválido.")
    try:
        cur.execute(
            """INSERT INTO usuarios (nombre, rut, email, rol, curso, asignatura, pie, password_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (u.nombre.strip(), u.rut.strip(), u.email.strip(), rol_clean, u.curso.strip(), u.asignatura.strip(), bool(u.pie), hash_pwd(u.password))
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error al crear usuario (¿RUT duplicado?): {str(e)}")
    finally:
        cur.close()
        conn.close()
    return {"ok": True, "id": new_id}

@app.put("/api/usuarios/{user_id}/pie")
def toggle_pie(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET pie = NOT COALESCE(pie, FALSE) WHERE id = %s RETURNING pie", (user_id,))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True, "pie": row["pie"] if row else False}

@app.delete("/api/usuarios/{user_id}")
def eliminar_usuario(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE id = %s AND rol != 'maestro'", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/alertas")
def crear_alerta(a: AlertaCreate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO alertas (titulo, detalle, nivel) VALUES (%s, %s, %s)", (a.titulo.strip(), a.detalle.strip(), a.nivel))
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

# --- ENDPOINTS DOCENTE / PROFESOR JEFE ---
@app.get("/api/docente/panel")
def panel_docente(cursos_asignados: str = ""):
    conn = get_db()
    cur = conn.cursor()
    lista_cursos = [c.strip() for c in cursos_asignados.split(",") if c.strip()]

    if lista_cursos:
        cur.execute(
            "SELECT id, nombre, rut, curso, pie FROM usuarios WHERE rol = 'estudiante' AND curso = ANY(%s) ORDER BY curso, nombre",
            (lista_cursos,)
        )
    else:
        cur.execute("SELECT id, nombre, rut, curso, pie FROM usuarios WHERE rol = 'estudiante' ORDER BY curso, nombre")
    estudiantes = cur.fetchall()

    est_ids = [e["id"] for e in estudiantes]
    notas = []
    asistencias = []
    justificaciones = []
    anotaciones = []
    entrevistas = []

    if est_ids:
        cur.execute("""
            SELECT c.id, c.estudiante_id, u.nombre AS estudiante, u.curso, c.asignatura, c.evaluacion, c.nota, c.ponderacion, c.fecha
            FROM calificaciones c JOIN usuarios u ON c.estudiante_id = u.id
            WHERE c.estudiante_id = ANY(%s) ORDER BY c.id DESC
        """, (est_ids,))
        notas = cur.fetchall()

        cur.execute("""
            SELECT estudiante_id, estado FROM asistencia WHERE estudiante_id = ANY(%s)
        """, (est_ids,))
        asistencias = cur.fetchall()

        cur.execute("""
            SELECT j.id, j.estudiante_id, u.nombre AS estudiante, u.rut, j.curso, j.fecha_ausencia, j.motivo, j.estado, j.respuesta_docente, j.creado_en
            FROM justificaciones j JOIN usuarios u ON j.estudiante_id = u.id
            WHERE j.estudiante_id = ANY(%s)
            ORDER BY CASE WHEN j.estado = 'Pendiente' THEN 0 ELSE 1 END, j.creado_en DESC
        """, (est_ids,))
        justificaciones = cur.fetchall()

        cur.execute("""
            SELECT a.id, a.estudiante_id, u.nombre AS estudiante, u.curso, a.docente_nombre, a.tipo, a.detalle, a.fecha
            FROM anotaciones a JOIN usuarios u ON a.estudiante_id = u.id
            WHERE a.estudiante_id = ANY(%s) ORDER BY a.id DESC LIMIT 50
        """, (est_ids,))
        anotaciones = cur.fetchall()

        cur.execute("""
            SELECT e.id, e.estudiante_id, u.nombre AS estudiante, u.curso, e.docente_nombre, e.apoderado, e.motivo, e.acuerdos, e.fecha
            FROM entrevistas e JOIN usuarios u ON e.estudiante_id = u.id
            WHERE e.estudiante_id = ANY(%s) ORDER BY e.id DESC LIMIT 50
        """, (est_ids,))
        entrevistas = cur.fetchall()

    # Calcular resumen Decreto 67 (promedio + % asistencia + alertas de riesgo) por estudiante
    resumen_estudiantes = []
    for est in estudiantes:
        eid = est["id"]
        n_est = [x for x in notas if x["estudiante_id"] == eid]
        # Promedio por asignatura
        por_asig = {}
        for x in n_est:
            por_asig.setdefault(x["asignatura"], []).append((float(x["nota"]), int(x["ponderacion"] or 100)))
        proms_asig = {}
        for asig, vals in por_asig.items():
            total_w = sum(w for _, w in vals)
            if total_w > 0:
                proms_asig[asig] = round(sum(n * w for n, w in vals) / total_w, 1)
            else:
                proms_asig[asig] = round(sum(n for n, _ in vals) / len(vals), 1)

        prom_gral = round(sum(proms_asig.values()) / len(proms_asig), 1) if proms_asig else None
        rojos = sum(1 for p in proms_asig.values() if p < 4.0)

        a_est = [x for x in asistencias if x["estudiante_id"] == eid]
        total_dias = len(a_est)
        dias_validos = sum(1 for x in a_est if x["estado"] in ("Presente", "Atrasado", "Justificado"))
        pct_asistencia = round((dias_validos / total_dias) * 100, 1) if total_dias > 0 else 100.0

        riesgo_d67 = False
        motivos_riesgo = []
        if prom_gral is not None and prom_gral < 4.0:
            riesgo_d67 = True
            motivos_riesgo.append(f"Promedio bajo 4.0 ({prom_gral})")
        if rojos >= 2:
            riesgo_d67 = True
            motivos_riesgo.append(f"{rojos} asignaturas insuficientes")
        if total_dias > 0 and pct_asistencia < 85.0:
            riesgo_d67 = True
            motivos_riesgo.append(f"Asistencia crítica ({pct_asistencia}% < 85%)")

        resumen_estudiantes.append({
            "id": eid,
            "nombre": est["nombre"],
            "rut": est["rut"],
            "curso": est["curso"],
            "pie": bool(est["pie"]),
            "promedio_general": prom_gral,
            "promedios_asignatura": proms_asig,
            "rojos": rojos,
            "pct_asistencia": pct_asistencia,
            "total_dias_asistencia": total_dias,
            "riesgo_d67": riesgo_d67,
            "motivos_riesgo": motivos_riesgo
        })

    cur.close()
    conn.close()
    return {
        "cursos_disponibles": lista_cursos,
        "estudiantes": resumen_estudiantes,
        "notas": notas[:60],
        "justificaciones": justificaciones,
        "anotaciones": anotaciones,
        "entrevistas": entrevistas
    }

@app.post("/api/docente/notas")
def registrar_nota(n: NotaCreate):
    if n.nota < 1.0 or n.nota > 7.0:
        raise HTTPException(status_code=400, detail="La nota debe estar entre 1.0 y 7.0 (Decreto 67).")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO calificaciones (estudiante_id, asignatura, evaluacion, nota, ponderacion) VALUES (%s, %s, %s, %s, %s)",
        (n.estudiante_id, n.asignatura.strip(), n.evaluacion.strip(), round(n.nota, 1), n.ponderacion)
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.delete("/api/docente/notas/{nota_id}")
def eliminar_nota(nota_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM calificaciones WHERE id = %s", (nota_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/docente/asistencia-masiva")
def registrar_asistencia_masiva(data: AsistenciaMasivaCreate):
    conn = get_db()
    cur = conn.cursor()
    for reg in data.registros:
        cur.execute(
            """INSERT INTO asistencia (estudiante_id, curso, fecha, estado)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (estudiante_id, fecha) DO UPDATE SET estado = EXCLUDED.estado""",
            (reg.estudiante_id, data.curso.strip(), data.fecha, reg.estado.strip())
        )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.put("/api/docente/justificaciones/{just_id}")
def resolver_justificacion(just_id: int, data: JustificacionResolver):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE justificaciones SET estado = %s, respuesta_docente = %s WHERE id = %s RETURNING estudiante_id, curso, fecha_ausencia",
        (data.estado, data.respuesta_docente.strip(), just_id)
    )
    row = cur.fetchone()
    if row and data.estado == "Aprobada":
        cur.execute(
            """INSERT INTO asistencia (estudiante_id, curso, fecha, estado)
               VALUES (%s, %s, %s, 'Justificado')
               ON CONFLICT (estudiante_id, fecha) DO UPDATE SET estado = 'Justificado'""",
            (row["estudiante_id"], row["curso"], row["fecha_ausencia"])
        )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/docente/anotaciones")
def crear_anotacion(a: AnotacionCreate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO anotaciones (estudiante_id, docente_nombre, tipo, detalle) VALUES (%s, %s, %s, %s)",
        (a.estudiante_id, a.docente_nombre.strip(), a.tipo.strip(), a.detalle.strip())
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/docente/entrevistas")
def crear_entrevista(e: EntrevistaCreate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO entrevistas (estudiante_id, docente_nombre, apoderado, motivo, acuerdos) VALUES (%s, %s, %s, %s, %s)",
        (e.estudiante_id, e.docente_nombre.strip(), e.apoderado.strip(), e.motivo.strip(), e.acuerdos.strip())
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

# --- ENDPOINTS ESTUDIANTE ---
@app.get("/api/estudiante/{estudiante_id}")
def datos_estudiante(estudiante_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, rut, email, curso FROM usuarios WHERE id = %s AND rol = 'estudiante'", (estudiante_id,))
    est = cur.fetchone()
    if not est:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Estudiante no encontrado")

    cur.execute("SELECT asignatura, evaluacion, nota, ponderacion, fecha FROM calificaciones WHERE estudiante_id = %s ORDER BY asignatura, fecha DESC", (estudiante_id,))
    notas = cur.fetchall()

    # Calcular promedios por asignatura y general (Decreto 67)
    por_asig = {}
    for x in notas:
        por_asig.setdefault(x["asignatura"], []).append((float(x["nota"]), int(x["ponderacion"] or 100)))
    proms_asig = {}
    for asig, vals in por_asig.items():
        total_w = sum(w for _, w in vals)
        proms_asig[asig] = round(sum(n * w for n, w in vals) / total_w, 1) if total_w > 0 else round(sum(n for n, _ in vals) / len(vals), 1)
    prom_gral = round(sum(proms_asig.values()) / len(proms_asig), 1) if proms_asig else None

    cur.execute("SELECT fecha, estado FROM asistencia WHERE estudiante_id = %s ORDER BY fecha DESC LIMIT 60", (estudiante_id,))
    asistencia = cur.fetchall()
    total_dias = len(asistencia)
    dias_validos = sum(1 for x in asistencia if x["estado"] in ("Presente", "Atrasado", "Justificado"))
    pct_asistencia = round((dias_validos / total_dias) * 100, 1) if total_dias > 0 else 100.0

    cur.execute("SELECT id, fecha_ausencia, motivo, estado, respuesta_docente, creado_en FROM justificaciones WHERE estudiante_id = %s ORDER BY creado_en DESC", (estudiante_id,))
    justificaciones = cur.fetchall()

    cur.execute("SELECT docente_nombre, tipo, detalle, fecha FROM anotaciones WHERE estudiante_id = %s ORDER BY id DESC", (estudiante_id,))
    anotaciones = cur.fetchall()

    cur.execute("SELECT titulo, detalle, nivel, creado_en FROM alertas ORDER BY creado_en DESC LIMIT 10")
    alertas = cur.fetchall()

    cur.execute("SELECT nombre FROM usuarios WHERE rol = 'docente' AND curso LIKE %s LIMIT 1", (f"%{est['curso']}%",))
    prof = cur.fetchone()

    cur.close()
    conn.close()
    return {
        "estudiante": est,
        "profesor_jefe": prof["nombre"] if prof else "No asignado",
        "notas": notas,
        "promedios_asignatura": proms_asig,
        "promedio_general": prom_gral,
        "asistencia": asistencia,
        "pct_asistencia": pct_asistencia,
        "justificaciones": justificaciones,
        "anotaciones": anotaciones,
        "alertas": alertas
    }

@app.post("/api/estudiante/justificar")
def enviar_justificacion(j: JustificacionCreate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO justificaciones (estudiante_id, curso, fecha_ausencia, motivo) VALUES (%s, %s, %s, %s)",
        (j.estudiante_id, j.curso.strip(), j.fecha_ausencia, j.motivo.strip())
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/admin/promocion-anual")
def promocion_anual():
    mapa = {
        "1° Básico": "2° Básico",
        "2° Básico": "3° Básico",
        "3° Básico": "4° Básico",
        "4° Básico": "5° Básico",
        "5° Básico": "6° Básico",
        "6° Básico": "7° Básico",
        "7° Básico": "8° Básico",
        "8° Básico": "Egresado"
    }
    conn = get_db()
    cur = conn.cursor()
    for actual, siguiente in mapa.items():
        cur.execute("UPDATE usuarios SET curso = %s WHERE rol = 'estudiante' AND curso = %s", (siguiente, actual))
    cur.execute("DELETE FROM calificaciones")
    cur.execute("DELETE FROM asistencia")
    cur.execute("DELETE FROM justificaciones")
    cur.execute("DELETE FROM anotaciones")
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True, "mensaje": "Promoción de curso y reinicio anual ejecutados correctamente."}
