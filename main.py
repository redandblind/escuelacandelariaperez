import os
from datetime import datetime
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_HfWGOv9E2DQL@ep-restless-wave-be8pxmm9-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)

app = FastAPI(title="API Portal Escolar - Escuela Candelaria Pérez")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Falta configurar DATABASE_URL")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alumnos (
        rut TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        curso TEXT NOT NULL,
        activo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS docentes (
        id SERIAL PRIMARY KEY,
        nombre TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        cursos TEXT NOT NULL,
        activo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS notas (
        id SERIAL PRIMARY KEY,
        rut TEXT NOT NULL,
        asignatura TEXT NOT NULL,
        nota REAL NOT NULL,
        semestre INTEGER NOT NULL,
        fecha TEXT NOT NULL,
        docente TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS observaciones (
        id SERIAL PRIMARY KEY,
        rut TEXT NOT NULL,
        tipo TEXT NOT NULL,
        texto TEXT NOT NULL,
        docente TEXT NOT NULL,
        fecha TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS justificaciones (
        id SERIAL PRIMARY KEY,
        rut TEXT NOT NULL,
        curso TEXT NOT NULL,
        mensaje TEXT NOT NULL,
        fecha TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS anuncios (
        id SERIAL PRIMARY KEY,
        curso TEXT NOT NULL,
        texto TEXT NOT NULL,
        docente TEXT NOT NULL,
        fecha TEXT NOT NULL
    );
    """)
    conn.commit()
    cursor.close()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

class LoginRequest(BaseModel):
    rol: str
    identificador: str
    password: Optional[str] = None

class AlumnoCreate(BaseModel):
    rut: str
    nombre: str
    curso: str

class NotaCreate(BaseModel):
    rut: str
    asignatura: str
    nota: float
    semestre: int
    docente: str

class ObservacionCreate(BaseModel):
    rut: str
    tipo: str
    texto: str
    docente: str

class JustificacionCreate(BaseModel):
    rut: str
    curso: str
    mensaje: str

class AnuncioCreate(BaseModel):
    curso: str
    texto: str
    docente: str

@app.post("/api/login")
def login(data: LoginRequest):
    conn = get_db()
    cursor = conn.cursor()

    if data.rol == "docente":
        nombre_norm = data.identificador.strip().upper()
        cursor.execute("SELECT * FROM docentes WHERE UPPER(nombre) = %s AND activo = 1", (nombre_norm,))
        docente = cursor.fetchone()
        cursor.close()
        conn.close()

        if not docente or not data.password or not pwd_context.verify(data.password, docente["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenciales de docente inválidas")

        return {
            "rol": "docente",
            "nombre": docente["nombre"],
            "cursos": [c.strip() for c in docente["cursos"].split(",")]
        }

    elif data.rol == "estudiante":
        rut_limpio = data.identificador.strip().upper()
        cursor.execute("SELECT * FROM alumnos WHERE UPPER(rut) = %s AND activo = 1", (rut_limpio,))
        alumno = cursor.fetchone()
        cursor.close()
        conn.close()

        if not alumno:
            raise HTTPException(status_code=404, detail="Estudiante no encontrado o inactivo")

        return {
            "rol": "estudiante",
            "rut": alumno["rut"],
            "nombre": alumno["nombre"],
            "curso": alumno["curso"]
        }

    cursor.close()
    conn.close()
    raise HTTPException(status_code=400, detail="Rol no válido")

@app.get("/api/docente/panel")
def panel_docente(cursos: str):
    lista_cursos = [c.strip() for c in cursos.split(",") if c.strip()]
    if not lista_cursos:
        return {"alumnos": [], "notas": [], "justificaciones": []}
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM alumnos WHERE activo = 1 AND curso = ANY(%s) ORDER BY curso, nombre",
        (lista_cursos,)
    )
    alumnos = [dict(r) for r in cursor.fetchall()]
    ruts = [a["rut"] for a in alumnos]

    notas = []
    if ruts:
        cursor.execute("SELECT * FROM notas WHERE rut = ANY(%s)", (ruts,))
        notas = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        "SELECT * FROM justificaciones WHERE curso = ANY(%s) ORDER BY fecha DESC",
        (lista_cursos,)
    )
    justificaciones = [dict(r) for r in cursor.fetchall()]

    cursor.close()
    conn.close()
    return {
        "alumnos": alumnos,
        "notas": notas,
        "justificaciones": justificaciones
    }

@app.get("/api/alumnos")
def listar_alumnos(incluir_inactivos: bool = False):
    conn = get_db()
    cursor = conn.cursor()
    if incluir_inactivos:
        cursor.execute("SELECT * FROM alumnos ORDER BY curso, nombre")
    else:
        cursor.execute("SELECT * FROM alumnos WHERE activo = 1 ORDER BY curso, nombre")
    filas = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return filas

@app.post("/api/alumnos")
def crear_alumno(alumno: AlumnoCreate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO alumnos (rut, nombre, curso, activo) VALUES (%s, %s, %s, 1)",
            (alumno.rut.strip().upper(), alumno.nombre.strip().upper(), alumno.curso.strip())
        )
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="El RUT ya existe en la base de datos")
    cursor.close()
    conn.close()
    return {"mensaje": "Alumno creado correctamente"}

@app.patch("/api/alumnos/{rut}/estado")
def cambiar_estado_alumno(rut: str, activo: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE alumnos SET activo = %s WHERE rut = %s", (activo, rut))
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Estado actualizado"}

@app.get("/api/notas")
def obtener_notas(rut: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    if rut:
        cursor.execute("SELECT * FROM notas WHERE rut = %s", (rut,))
    else:
        cursor.execute("SELECT * FROM notas")
    filas = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return filas

@app.post("/api/notas")
def agregar_nota(nota: NotaCreate):
    if not (1.0 <= nota.nota <= 7.0):
        raise HTTPException(status_code=400, detail="La nota debe estar entre 1.0 y 7.0")
    conn = get_db()
    cursor = conn.cursor()
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO notas (rut, asignatura, nota, semestre, fecha, docente) VALUES (%s, %s, %s, %s, %s, %s)",
        (nota.rut, nota.asignatura, round(nota.nota, 1), nota.semestre, fecha_actual, nota.docente)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Nota registrada"}

@app.delete("/api/notas/{nota_id}")
def eliminar_nota(nota_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notas WHERE id = %s", (nota_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Nota eliminada"}

@app.get("/api/portal/{rut}")
def datos_estudiante(rut: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alumnos WHERE rut = %s", (rut,))
    alumno = cursor.fetchone()
    if not alumno:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Alumno no encontrado")

    curso = alumno["curso"]
    cursor.execute("SELECT * FROM notas WHERE rut = %s", (rut,))
    notas = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM observaciones WHERE rut = %s ORDER BY fecha DESC", (rut,))
    observaciones = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM justificaciones WHERE rut = %s ORDER BY fecha DESC", (rut,))
    justificaciones = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM anuncios WHERE curso = %s ORDER BY fecha DESC", (curso,))
    anuncios = [dict(r) for r in cursor.fetchall()]

    cursor.close()
    conn.close()
    return {
        "alumno": dict(alumno),
        "notas": notas,
        "observaciones": observaciones,
        "justificaciones": justificaciones,
        "anuncios": anuncios
    }

@app.post("/api/observaciones")
def crear_observacion(obs: ObservacionCreate):
    conn = get_db()
    cursor = conn.cursor()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO observaciones (rut, tipo, texto, docente, fecha) VALUES (%s, %s, %s, %s, %s)",
        (obs.rut, obs.tipo, obs.texto, obs.docente, fecha)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Observación guardada"}

@app.get("/api/justificaciones")
def listar_justificaciones():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM justificaciones ORDER BY fecha DESC")
    filas = [dict(r) for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return filas

@app.post("/api/justificaciones")
def crear_justificacion(just: JustificacionCreate):
    conn = get_db()
    cursor = conn.cursor()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO justificaciones (rut, curso, mensaje, fecha) VALUES (%s, %s, %s, %s)",
        (just.rut, just.curso, just.mensaje, fecha)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Justificación enviada"}

@app.post("/api/anuncios")
def crear_anuncio(anuncio: AnuncioCreate):
    conn = get_db()
    cursor = conn.cursor()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO anuncios (curso, texto, docente, fecha) VALUES (%s, %s, %s, %s)",
        (anuncio.curso, anuncio.texto, anuncio.docente, fecha)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"mensaje": "Anuncio publicado"}

app.mount("/", StaticFiles(directory=".", html=True), name="static")