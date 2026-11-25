import os
import hashlib
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Portal Escuela Candelaria Pérez v8.0")

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
            curso VARCHAR(80),
            asignatura VARCHAR(100),
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
            fecha DATE DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS asistencia (
            id SERIAL PRIMARY KEY,
            estudiante_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            curso VARCHAR(80) NOT NULL,
            fecha DATE DEFAULT CURRENT_DATE,
            estado VARCHAR(20) NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alertas (
            id SERIAL PRIMARY KEY,
            titulo VARCHAR(150) NOT NULL,
            detalle TEXT NOT NULL,
            nivel VARCHAR(30) DEFAULT 'info',
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
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
    password: str

class NotaCreate(BaseModel):
    estudiante_id: int
    asignatura: str
    evaluacion: str
    nota: float

class AsistenciaCreate(BaseModel):
    estudiante_id: int
    curso: str
    estado: str

class AlertaCreate(BaseModel):
    titulo: str
    detalle: str
    nivel: str = "info"

@app.get("/")
def health_check():
    return {"status": "ok", "version": "8.0", "app": "Escuela Candelaria Pérez"}

@app.post("/api/login")
def login(data: LoginRequest):
    conn = get_db()
    cur = conn.cursor()
    ident = data.identificador.strip()
    pwd_h = hash_pwd(data.password)

    if data.tipo_portal == "staff":
        cur.execute(
            """SELECT id, nombre, rut, email, rol, curso, asignatura 
               FROM usuarios 
               WHERE (LOWER(nombre) = LOWER(%s) OR LOWER(rut) = LOWER(%s) OR LOWER(email) = LOWER(%s))
               AND rol IN ('maestro', 'docente') AND password_hash = %s AND activo = TRUE""",
            (ident, ident, ident, pwd_h)
        )
    else:
        cur.execute(
            """SELECT id, nombre, rut, email, rol, curso, asignatura 
               FROM usuarios 
               WHERE (LOWER(rut) = LOWER(%s) OR LOWER(nombre) = LOWER(%s))
               AND rol = 'estudiante' AND password_hash = %s AND activo = TRUE""",
            (ident, ident, pwd_h)
        )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas o usuario no autorizado para este portal.")

    return {
        "ok": True,
        "usuario": {
            "id": user["id"],
            "nombre": user["nombre"],
            "rut": user["rut"],
            "email": user["email"],
            "rol": str(user["rol"]).strip().lower(),
            "curso": user["curso"] or "",
            "asignatura": user["asignatura"] or ""
        }
    }

@app.get("/api/dashboard/maestro")
def dashboard_maestro():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, rut, email, rol, curso, asignatura FROM usuarios ORDER BY rol, curso, nombre")
    usuarios = cur.fetchall()
    cur.execute("SELECT * FROM cursos ORDER BY nombre")
    cursos = cur.fetchall()
    cur.execute("SELECT * FROM alertas ORDER BY creado_en DESC LIMIT 20")
    alertas = cur.fetchall()
    cur.execute("SELECT ROUND(AVG(nota), 1) AS promedio FROM calificaciones")
    prom = cur.fetchone()["promedio"] or 0.0
    cur.close()
    conn.close()
    return {
        "usuarios": usuarios,
        "cursos": cursos,
        "alertas": alertas,
        "promedio_general": float(prom)
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
            """INSERT INTO usuarios (nombre, rut, email, rol, curso, asignatura, password_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (u.nombre.strip(), u.rut.strip(), u.email.strip(), rol_clean, u.curso.strip(), u.asignatura.strip(), hash_pwd(u.password))
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error al crear usuario: {str(e)}")
    finally:
        cur.close()
        conn.close()
    return {"ok": True, "id": new_id}

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

@app.get("/api/docente/datos")
def datos_docente(curso: Optional[str] = ""):
    conn = get_db()
    cur = conn.cursor()
    if curso:
        cur.execute("SELECT id, nombre, rut, curso FROM usuarios WHERE rol = 'estudiante' AND curso = %s ORDER BY nombre", (curso,))
    else:
        cur.execute("SELECT id, nombre, rut, curso FROM usuarios WHERE rol = 'estudiante' ORDER BY curso, nombre")
    estudiantes = cur.fetchall()
    cur.execute("SELECT * FROM cursos ORDER BY nombre")
    cursos = cur.fetchall()
    cur.execute("""
        SELECT c.id, u.nombre AS estudiante, u.curso, c.asignatura, c.evaluacion, c.nota, c.fecha
        FROM calificaciones c
        JOIN usuarios u ON c.estudiante_id = u.id
        ORDER BY c.id DESC LIMIT 50
    """)
    notas = cur.fetchall()
    cur.close()
    conn.close()
    return {"estudiantes": estudiantes, "cursos": cursos, "notas": notas}

@app.post("/api/docente/notas")
def registrar_nota(n: NotaCreate):
    if n.nota < 1.0 or n.nota > 7.0:
        raise HTTPException(status_code=400, detail="La nota debe estar entre 1.0 y 7.0")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO calificaciones (estudiante_id, asignatura, evaluacion, nota) VALUES (%s, %s, %s, %s)",
        (n.estudiante_id, n.asignatura.strip(), n.evaluacion.strip(), round(n.nota, 1))
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

@app.post("/api/docente/asistencia")
def registrar_asistencia(a: AsistenciaCreate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO asistencia (estudiante_id, curso, estado) VALUES (%s, %s, %s)",
        (a.estudiante_id, a.curso.strip(), a.estado.strip())
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}

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
    cur.execute("SELECT asignatura, evaluacion, nota, fecha FROM calificaciones WHERE estudiante_id = %s ORDER BY asignatura, fecha DESC", (estudiante_id,))
    notas = cur.fetchall()
    cur.execute("SELECT fecha, estado FROM asistencia WHERE estudiante_id = %s ORDER BY fecha DESC LIMIT 30", (estudiante_id,))
    asistencia = cur.fetchall()
    cur.execute("SELECT titulo, detalle, nivel, creado_en FROM alertas ORDER BY creado_en DESC LIMIT 10")
    alertas = cur.fetchall()
    cur.close()
    conn.close()
    return {"estudiante": est, "notas": notas, "asistencia": asistencia, "alertas": alertas}

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
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True, "mensaje": "Promoción de curso y reinicio anual ejecutados correctamente."}
