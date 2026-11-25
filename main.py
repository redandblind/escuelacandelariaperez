import os
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
SECRET_KEY = (os.getenv("SECRET_KEY") or "ecp-sanjavier-secret-2026-clave-interna").strip()
CHILE_TZ = ZoneInfo("America/Santiago")

app = FastAPI(title="API Portal Escolar v8.0 - Escuela Candelaria Pérez")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
db_pool: Optional[pool.SimpleConnectionPool] = None

CURSOS_INICIALES = [
    ("1° Básico", 1),
    ("2° Básico", 2),
    ("3° Básico", 3),
    ("4° Básico", 4),
    ("5° Básico", 5),
    ("6° Básico", 6),
    ("7° Básico", 7),
    ("8° Básico", 8),
]

ASIGNATURAS_INICIALES = [
    "Lenguaje y Comunicación",
    "Matemática",
    "Historia, Geografía y Ciencias Sociales",
    "Ciencias Naturales",
    "Artes Visuales",
    "Educación Física y Salud",
    "Tecnología",
    "Música",
    "Inglés",
    "Religión",
    "Orientación"
]

def ahora_chile() -> datetime:
    return datetime.now(CHILE_TZ)

def normalizar_rut(rut: str) -> str:
    limpio = rut.strip().upper().replace(".", "").replace(" ", "")
    if "-" not in limpio and len(limpio) >= 2:
        limpio = f"{limpio[:-1]}-{limpio[-1]}"
    return limpio

def validar_rut_modulo11(rut: str) -> bool:
    rut_norm = normalizar_rut(rut)
    if "-" not in rut_norm:
        return False
    cuerpo, dv = rut_norm.split("-", 1)
    if not cuerpo.isdigit() or not (7 <= len(cuerpo) <= 8) or dv not in "0123456789K":
        return False
    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1
    resto = 11 - (suma % 11)
    dv_esperado = "0" if resto == 11 else ("K" if resto == 10 else str(resto))
    return dv == dv_esperado

def crear_token_usuario(nombre: str, rol: str, cursos: list) -> str:
    payload = {
        "rol": rol,
        "nombre": nombre,
        "cursos": cursos,
        "exp": int((ahora_chile() + timedelta(hours=12)).timestamp())
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"

def verificar_auth(authorization: Optional[str] = Header(None), roles_permitidos: list = ["docente", "maestro"]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sesión requerida")
    token = authorization.split(" ", 1)[1].strip()
    if "." not in token:
        raise HTTPException(status_code=401, detail="Token inválido")
    raw, sig = token.split(".", 1)
    expected = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Firma de token inválida")
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception:
        raise HTTPException(status_code=401, detail="Token corrupto")
    rol = payload.get("rol")
    if rol not in roles_permitidos or payload.get("exp", 0) < int(ahora_chile().timestamp()):
        raise HTTPException(status_code=403, detail="No tienes permisos suficientes o la sesión expiró")
    return payload

def get_pool():
    global db_pool
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Falta configurar DATABASE_URL en Render (Environment)")
    if db_pool is None:
        for intento in range(3):
            try:
                db_pool = pool.SimpleConnectionPool(
                    1, 10, DATABASE_URL, cursor_factory=RealDictCursor
                )
                break
            except Exception as e:
                if intento == 2:
                    raise HTTPException(status_code=503, detail=f"Base de datos despertando, intenta nuevamente: {str(e)}")
                time.sleep(1.5)
    return db_pool

class DBConnection:
    def __init__(self):
        self.pool = get_pool()
        self.conn = self.pool.getconn()
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            try:
                self.pool.putconn(self.conn, close=True)
            except Exception:
                pass
            self.conn = self.pool.getconn()

    def cursor(self):
        return self.conn.cursor()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        if self.conn:
            try:
                self.pool.putconn(self.conn)
            except Exception:
                pass
            self.conn = None

def registrar_log(cursor, usuario: str, accion: str, detalle: str):
    try:
        cursor.execute(
            "INSERT INTO auditoria_logs (fecha, usuario, accion, detalle) VALUES (%s, %s, %s, %s)",
            (ahora_chile().strftime("%Y-%m-%d %H:%M:%S"), usuario, accion, detalle)
        )
    except Exception:
        pass

def siguiente_curso_db(cursor, curso_actual: str) -> str:
    cursor.execute("SELECT nombre, orden FROM cursos ORDER BY orden ASC, id ASC")
    lista = [dict(r) for r in cursor.fetchall()]
    nombres = [c["nombre"] for c in lista]
    if curso_actual in nombres:
        idx = nombres.index(curso_actual)
        if idx + 1 < len(nombres):
            return nombres[idx + 1]
        return "EGRESADO"
    return curso_actual

def calcular_promedio_alumno(notas_alumno: list) -> Optional[float]:
    by_asig = {}
    for n in notas_alumno:
        try:
            val = float(n["nota"])
            if 1.0 <= val <= 7.0:
                by_asig.setdefault(n["asignatura"], []).append(val)
        except Exception:
            continue
    if not by_asig:
        return None
    promedios_asig = [sum(vals) / len(vals) for vals in by_asig.values()]
    return round(sum(promedios_asig) / len(promedios_asig), 1)

def ejecutar_promocion_anual(cursor, anio: int, ejecutor: str = "SISTEMA AUTOMÁTICO"):
    cursor.execute("SELECT * FROM alumnos WHERE activo = 1")
    alumnos = [dict(r) for r in cursor.fetchall()]

    for al in alumnos:
        rut = al["rut"]
        curso_actual = al["curso"]
        cursor.execute("SELECT asignatura, nota FROM notas WHERE rut = %s", (rut,))
        notas = [dict(r) for r in cursor.fetchall()]
        prom = calcular_promedio_alumno(notas)

        aprueba = (prom is None) or (prom >= 4.0)
        nuevo_curso = siguiente_curso_db(cursor, curso_actual) if aprueba else curso_actual
        estado_final = "PROMOVIDO" if aprueba else "REPITE"

        if nuevo_curso == "EGRESADO":
            estado_final = "EGRESADO"
            cursor.execute("UPDATE alumnos SET activo = 0 WHERE rut = %s", (rut,))
        else:
            cursor.execute("UPDATE alumnos SET curso = %s WHERE rut = %s", (nuevo_curso, rut))

        cursor.execute(
            """
            INSERT INTO historial_anual (anio, rut, nombre, curso_anterior, curso_nuevo, promedio_final, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (anio, rut, al["nombre"], curso_actual, nuevo_curso, prom, estado_final)
        )

    cursor.execute(
        "UPDATE configuracion_calendario SET ultimo_anio_promocion = %s WHERE id = 1",
        (anio,)
    )
    registrar_log(cursor, ejecutor, "CIERRE_ANUAL", f"Promoción anual {anio} ejecutada para {len(alumnos)} alumnos.")

def ejecutar_limpieza_enero(cursor, anio_nuevo: int):
    cursor.execute("DELETE FROM notas")
    cursor.execute("DELETE FROM anuncios")
    cursor.execute("DELETE FROM justificaciones")
    cursor.execute("DELETE FROM asistencia")
    cursor.execute(
        "UPDATE configuracion_calendario SET ultimo_anio_limpieza = %s WHERE id = 1",
        (anio_nuevo,)
    )
    registrar_log(cursor, "SISTEMA AUTOMÁTICO", "LIMPIEZA_ENERO", f"Limpieza anual 1 de enero {anio_nuevo}.")

def verificar_automatizacion_calendario():
    if not DATABASE_URL:
        return
    ahora = ahora_chile()
    anio = ahora.year
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM configuracion_calendario WHERE id = 1")
        cfg = cursor.fetchone()
        if not cfg:
            return
        mes_cierre = cfg["mes_cierre"]
        dia_cierre = cfg["dia_cierre"]
        ultimo_prom = cfg["ultimo_anio_promocion"]
        ultimo_limp = cfg["ultimo_anio_limpieza"]

        if (ahora.month == 1 and ahora.day >= 1) and ultimo_limp < anio:
            if ultimo_prom < (anio - 1):
                ejecutar_promocion_anual(cursor, anio - 1)
            ejecutar_limpieza_enero(cursor, anio)
            conn.commit()

        if (ahora.month > mes_cierre or (ahora.month == mes_cierre and ahora.day >= dia_cierre)) and ultimo_prom < anio:
            ejecutar_promocion_anual(cursor, anio)
            conn.commit()
    except Exception as e:
        conn.rollback()
        print("Advertencia calendario:", e)
    finally:
        cursor.close()
        conn.close()

def init_db():
    if not DATABASE_URL:
        return
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        sentencias = [
            """CREATE TABLE IF NOT EXISTS cursos (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                orden INTEGER DEFAULT 10
            )""",
            """CREATE TABLE IF NOT EXISTS asignaturas (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                activa INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS alumnos (
                rut TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                curso TEXT NOT NULL,
                activo INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS docentes (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                cursos TEXT NOT NULL,
                rol TEXT DEFAULT 'docente',
                activo INTEGER DEFAULT 1
            )""",
            "ALTER TABLE docentes ADD COLUMN IF NOT EXISTS rol TEXT DEFAULT 'docente'",
            "ALTER TABLE docentes ADD COLUMN IF NOT EXISTS activo INTEGER DEFAULT 1",
            """CREATE TABLE IF NOT EXISTS notas (
                id SERIAL PRIMARY KEY,
                rut TEXT NOT NULL,
                asignatura TEXT NOT NULL,
                nota REAL NOT NULL,
                semestre INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                docente TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS asistencia (
                id SERIAL PRIMARY KEY,
                rut TEXT NOT NULL,
                curso TEXT NOT NULL,
                fecha TEXT NOT NULL,
                estado TEXT NOT NULL,
                docente TEXT NOT NULL,
                UNIQUE(rut, fecha)
            )""",
            """CREATE TABLE IF NOT EXISTS observaciones (
                id SERIAL PRIMARY KEY,
                rut TEXT NOT NULL,
                tipo TEXT NOT NULL,
                texto TEXT NOT NULL,
                docente TEXT NOT NULL,
                fecha TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS justificaciones (
                id SERIAL PRIMARY KEY,
                rut TEXT NOT NULL,
                curso TEXT NOT NULL,
                mensaje TEXT NOT NULL,
                fecha TEXT NOT NULL,
                leida INTEGER DEFAULT 0
            )""",
            "ALTER TABLE justificaciones ADD COLUMN IF NOT EXISTS leida INTEGER DEFAULT 0",
            """CREATE TABLE IF NOT EXISTS anuncios (
                id SERIAL PRIMARY KEY,
                curso TEXT NOT NULL,
                texto TEXT NOT NULL,
                docente TEXT NOT NULL,
                fecha TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS auditoria_logs (
                id SERIAL PRIMARY KEY,
                fecha TEXT NOT NULL,
                usuario TEXT NOT NULL,
                accion TEXT NOT NULL,
                detalle TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS configuracion_calendario (
                id INTEGER PRIMARY KEY,
                mes_cierre INTEGER DEFAULT 12,
                dia_cierre INTEGER DEFAULT 20,
                ultimo_anio_promocion INTEGER DEFAULT 2025,
                ultimo_anio_limpieza INTEGER DEFAULT 2026
            )""",
            """CREATE TABLE IF NOT EXISTS historial_anual (
                id SERIAL PRIMARY KEY,
                anio INTEGER NOT NULL,
                rut TEXT NOT NULL,
                nombre TEXT NOT NULL,
                curso_anterior TEXT NOT NULL,
                curso_nuevo TEXT NOT NULL,
                promedio_final REAL,
                estado TEXT NOT NULL
            )"""
        ]
        for sql in sentencias:
            cursor.execute(sql)

        # Poblar cursos iniciales si está vacío
        cursor.execute("SELECT COUNT(*) AS total FROM cursos")
        if cursor.fetchone()["total"] == 0:
            for c_nom, c_ord in CURSOS_INICIALES:
                cursor.execute("INSERT INTO cursos (nombre, orden) VALUES (%s, %s) ON CONFLICT DO NOTHING", (c_nom, c_ord))

        # Poblar asignaturas iniciales si está vacío
        cursor.execute("SELECT COUNT(*) AS total FROM asignaturas")
        if cursor.fetchone()["total"] == 0:
            for a_nom in ASIGNATURAS_INICIALES:
                cursor.execute("INSERT INTO asignaturas (nombre, activa) VALUES (%s, 1) ON CONFLICT DO NOTHING", (a_nom,))

        # Crear perfil maestro oficial de la Directora: Paulina Toledo / escuela16
        cursor.execute("SELECT id FROM docentes WHERE UPPER(nombre) = 'PAULINA TOLEDO'")
        if not cursor.fetchone():
            pwd_hash = pwd_context.hash("escuela16")
            cursor.execute(
                "INSERT INTO docentes (nombre, password_hash, cursos, rol, activo) VALUES (%s, %s, %s, 'maestro', 1)",
                ("PAULINA TOLEDO", pwd_hash, "*")
            )

        anio_actual = ahora_chile().year
        cursor.execute(
            """
            INSERT INTO configuracion_calendario (id, mes_cierre, dia_cierre, ultimo_anio_promocion, ultimo_anio_limpieza)
            VALUES (1, 12, 20, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (anio_actual - 1, anio_actual)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("Advertencia init_db:", e)
    finally:
        cursor.close()
        conn.close()
    verificar_automatizacion_calendario()

@app.on_event("startup")
def startup_event():
    init_db()

# --- MODELOS PYDANTIC ---
class LoginRequest(BaseModel):
    rol: str
    identificador: str
    password: Optional[str] = None

class CursoCreate(BaseModel):
    nombre: str
    orden: int = 10

class AsignaturaCreate(BaseModel):
    nombre: str

class AlumnoCreate(BaseModel):
    rut: str
    nombre: str
    curso: str

class AlumnoUpdate(BaseModel):
    nombre: str
    curso: str

class AlumnoBulkItem(BaseModel):
    rut: str
    nombre: str
    curso: str

class AlumnoBulkImport(BaseModel):
    alumnos: List[AlumnoBulkItem]

class ReasignarMasivo(BaseModel):
    ruts: List[str]
    nuevo_curso: str

class DocenteCreate(BaseModel):
    nombre: str
    password: str
    cursos: str
    rol: Optional[str] = "docente"

class DocenteUpdate(BaseModel):
    nombre: str
    cursos: str
    password: Optional[str] = None
    rol: Optional[str] = "docente"
    activo: Optional[int] = 1

class NotaCreate(BaseModel):
    rut: str
    asignatura: str
    nota: float
    semestre: int
    docente: str

class AsistenciaItem(BaseModel):
    rut: str
    estado: str # P, A, T, J

class AsistenciaBatch(BaseModel):
    curso: str
    fecha: str
    docente: str
    registros: List[AsistenciaItem]

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
    curso: str # o "TODOS"
    texto: str
    docente: str

class CalendarioConfigUpdate(BaseModel):
    mes_cierre: int
    dia_cierre: int

# --- ENDPOINTS GENERALES ---
@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "8.0", "hora_chile": ahora_chile().strftime("%Y-%m-%d %H:%M:%S")}

@app.get("/api/catalogos")
def obtener_catalogos():
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, nombre, orden FROM cursos ORDER BY orden ASC, id ASC")
        cursos = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, nombre, activa FROM asignaturas WHERE activa = 1 ORDER BY id ASC")
        asignaturas = [dict(r) for r in cursor.fetchall()]
        return {"cursos": cursos, "asignaturas": asignaturas}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/login")
def login(data: LoginRequest):
    verificar_automatizacion_calendario()
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        if data.rol in ["docente", "maestro"]:
            nombre_norm = data.identificador.strip().upper()
            cursor.execute("SELECT * FROM docentes WHERE UPPER(nombre) = %s AND activo = 1", (nombre_norm,))
            docente = cursor.fetchone()
            if not docente or not data.password or not pwd_context.verify(data.password, docente["password_hash"]):
                raise HTTPException(status_code=401, detail="Nombre o contraseña incorrectos")

            rol_asignado = docente.get("rol") or "docente"
            if docente["cursos"].strip() == "*" or rol_asignado == "maestro":
                cursor.execute("SELECT nombre FROM cursos ORDER BY orden ASC, id ASC")
                cursos_lista = [r["nombre"] for r in cursor.fetchall()]
            else:
                cursos_lista = [c.strip() for c in docente["cursos"].split(",") if c.strip()]

            token = crear_token_usuario(docente["nombre"], rol_asignado, cursos_lista)
            registrar_log(cursor, docente["nombre"], "LOGIN", f"Inicio de sesión ({rol_asignado})")
            conn.commit()
            return {
                "rol": rol_asignado,
                "nombre": docente["nombre"],
                "cursos": cursos_lista,
                "token": token
            }

        elif data.rol == "estudiante":
            rut_limpio = normalizar_rut(data.identificador)
            cursor.execute("SELECT * FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s AND activo = 1", (rut_limpio,))
            alumno = cursor.fetchone()
            if not alumno:
                raise HTTPException(status_code=404, detail="Estudiante no encontrado o inactivo")
            return {
                "rol": "estudiante",
                "rut": alumno["rut"],
                "nombre": alumno["nombre"],
                "curso": alumno["curso"]
            }

        raise HTTPException(status_code=400, detail="Rol no válido")
    finally:
        cursor.close()
        conn.close()

# --- APIS MAESTRO (DIRECCIÓN) ---
@app.get("/api/maestro/resumen")
def resumen_maestro(user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM alumnos ORDER BY curso, nombre")
        alumnos = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM notas")
        notas = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM observaciones")
        observaciones = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM asistencia")
        asistencias = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, nombre, cursos, rol, activo FROM docentes ORDER BY rol DESC, nombre ASC")
        docentes = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, nombre, orden FROM cursos ORDER BY orden ASC, id ASC")
        cursos = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id, nombre, activa FROM asignaturas ORDER BY id ASC")
        asignaturas = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM auditoria_logs ORDER BY id DESC LIMIT 80")
        logs = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM justificaciones ORDER BY leida ASC, fecha DESC")
        justificaciones = [dict(r) for r in cursor.fetchall()]

        return {
            "alumnos": alumnos,
            "notas": notas,
            "observaciones": observaciones,
            "asistencias": asistencias,
            "docentes": docentes,
            "cursos": cursos,
            "asignaturas": asignaturas,
            "logs": logs,
            "justificaciones": justificaciones,
            "fecha_chile": ahora_chile().strftime("%Y-%m-%d")
        }
    finally:
        cursor.close()
        conn.close()

@app.post("/api/maestro/cursos")
def crear_curso_maestro(datos: CursoCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="Nombre de curso requerido")
    try:
        cursor.execute("INSERT INTO cursos (nombre, orden) VALUES (%s, %s)", (nombre, datos.orden))
        registrar_log(cursor, user["nombre"], "CREAR_CURSO", f"Creó curso: {nombre}")
        conn.commit()
        return {"mensaje": "Curso creado"}
    except Exception:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Ese curso ya existe")
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/maestro/cursos/{curso_id}")
def eliminar_curso_maestro(curso_id: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre FROM cursos WHERE id = %s", (curso_id,))
        c = cursor.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Curso no encontrado")
        cursor.execute("SELECT COUNT(*) AS total FROM alumnos WHERE curso = %s AND activo = 1", (c["nombre"],))
        if cursor.fetchone()["total"] > 0:
            raise HTTPException(status_code=400, detail="No puedes eliminar un curso que tiene alumnos activos. Trasládalos primero.")
        cursor.execute("DELETE FROM cursos WHERE id = %s", (curso_id,))
        registrar_log(cursor, user["nombre"], "ELIMINAR_CURSO", f"Eliminó curso: {c['nombre']}")
        conn.commit()
        return {"mensaje": "Curso eliminado"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/maestro/asignaturas")
def crear_asignatura_maestro(datos: AsignaturaCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="Nombre requerido")
    try:
        cursor.execute("INSERT INTO asignaturas (nombre, activa) VALUES (%s, 1) ON CONFLICT (nombre) DO UPDATE SET activa = 1", (nombre,))
        registrar_log(cursor, user["nombre"], "CREAR_ASIGNATURA", f"Activó/creó asignatura: {nombre}")
        conn.commit()
        return {"mensaje": "Asignatura guardada"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/maestro/asignaturas/{asig_id}")
def eliminar_asignatura_maestro(asig_id: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM asignaturas WHERE id = %s", (asig_id,))
        registrar_log(cursor, user["nombre"], "ELIMINAR_ASIGNATURA", f"Eliminó asignatura ID {asig_id}")
        conn.commit()
        return {"mensaje": "Asignatura eliminada"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/maestro/docentes")
def crear_docente_maestro(datos: DocenteCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        pwd_hash = pwd_context.hash(datos.password)
        nombre_norm = datos.nombre.strip().upper()
        rol_val = datos.rol if datos.rol in ["docente", "maestro"] else "docente"
        cursos_val = "*" if rol_val == "maestro" else datos.cursos.strip()
        cursor.execute(
            "INSERT INTO docentes (nombre, password_hash, cursos, rol, activo) VALUES (%s, %s, %s, %s, 1)",
            (nombre_norm, pwd_hash, cursos_val, rol_val)
        )
        registrar_log(cursor, user["nombre"], "CREAR_DOCENTE", f"Creó usuario {nombre_norm} ({rol_val})")
        conn.commit()
        return {"mensaje": "Perfil creado correctamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"Error al crear usuario (posible nombre duplicado): {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.put("/api/maestro/docentes/{docente_id}")
def actualizar_docente_maestro(docente_id: int, datos: DocenteUpdate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        nombre_norm = datos.nombre.strip().upper()
        rol_val = datos.rol if datos.rol in ["docente", "maestro"] else "docente"
        cursos_val = "*" if rol_val == "maestro" else datos.cursos.strip()
        if datos.password:
            pwd_hash = pwd_context.hash(datos.password)
            cursor.execute(
                "UPDATE docentes SET nombre = %s, password_hash = %s, cursos = %s, rol = %s, activo = %s WHERE id = %s",
                (nombre_norm, pwd_hash, cursos_val, rol_val, datos.activo if datos.activo is not None else 1, docente_id)
            )
        else:
            cursor.execute(
                "UPDATE docentes SET nombre = %s, cursos = %s, rol = %s, activo = %s WHERE id = %s",
                (nombre_norm, cursos_val, rol_val, datos.activo if datos.activo is not None else 1, docente_id)
            )
        registrar_log(cursor, user["nombre"], "EDITAR_DOCENTE", f"Editó usuario ID {docente_id} ({nombre_norm})")
        conn.commit()
        return {"mensaje": "Perfil actualizado correctamente"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/maestro/docentes/{docente_id}")
def eliminar_docente_maestro(docente_id: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT nombre FROM docentes WHERE id = %s", (docente_id,))
        doc = cursor.fetchone()
        if doc and doc["nombre"].upper() == user["nombre"].upper():
            raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta activa")
        cursor.execute("DELETE FROM docentes WHERE id = %s", (docente_id,))
        registrar_log(cursor, user["nombre"], "ELIMINAR_DOCENTE", f"Eliminó docente {doc['nombre'] if doc else docente_id}")
        conn.commit()
        return {"mensaje": "Usuario eliminado"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/maestro/alumnos/importar")
def importar_alumnos_masivo(datos: AlumnoBulkImport, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    creados = 0
    errores = []
    try:
        for item in datos.alumnos:
            rut_norm = normalizar_rut(item.rut)
            if not validar_rut_modulo11(rut_norm):
                errores.append(f"{item.rut}: RUT inválido")
                continue
            cursor.execute(
                """
                INSERT INTO alumnos (rut, nombre, curso, activo)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (rut) DO UPDATE SET nombre = EXCLUDED.nombre, curso = EXCLUDED.curso, activo = 1
                """,
                (rut_norm, item.nombre.strip().upper(), item.curso.strip())
            )
            creados += 1
        registrar_log(cursor, user["nombre"], "IMPORTACION_MASIVA", f"Importó/actualizó {creados} alumnos vía CSV.")
        conn.commit()
        return {"creados": creados, "errores": errores}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/maestro/alumnos/reasignar")
def reasignar_alumnos_curso(datos: ReasignarMasivo, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        ruts_norm = [normalizar_rut(r) for r in datos.ruts]
        cursor.execute("UPDATE alumnos SET curso = %s WHERE rut = ANY(%s)", (datos.nuevo_curso.strip(), ruts_norm))
        registrar_log(cursor, user["nombre"], "TRASLADO_CURSO", f"Trasladó {len(ruts_norm)} alumnos a {datos.nuevo_curso}")
        conn.commit()
        return {"mensaje": f"{len(ruts_norm)} alumnos trasladados a {datos.nuevo_curso}"}
    finally:
        cursor.close()
        conn.close()

# --- APIS ASISTENCIA ---
@app.get("/api/asistencia")
def obtener_asistencia(curso: str, fecha: str, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM asistencia WHERE curso = %s AND fecha = %s", (curso, fecha))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()

@app.post("/api/asistencia")
def guardar_asistencia(datos: AsistenciaBatch, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        for reg in datos.registros:
            rut_norm = normalizar_rut(reg.rut)
            cursor.execute(
                """
                INSERT INTO asistencia (rut, curso, fecha, estado, docente)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (rut, fecha) DO UPDATE SET estado = EXCLUDED.estado, docente = EXCLUDED.docente, curso = EXCLUDED.curso
                """,
                (rut_norm, datos.curso, datos.fecha, reg.estado, datos.docente)
            )
        registrar_log(cursor, datos.docente, "ASISTENCIA", f"Pasó asistencia {datos.curso} ({datos.fecha})")
        conn.commit()
        return {"mensaje": "Asistencia guardada"}
    finally:
        cursor.close()
        conn.close()

# --- APIS COMPARTIDAS (DOCENTE Y MAESTRO) ---
@app.get("/api/calendario")
def obtener_calendario(user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    verificar_automatizacion_calendario()
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM configuracion_calendario WHERE id = 1")
        cfg = dict(cursor.fetchone())
        cursor.execute("SELECT * FROM historial_anual ORDER BY anio DESC, curso_anterior, nombre LIMIT 200")
        historial = [dict(r) for r in cursor.fetchall()]
        return {"config": cfg, "fecha_servidor": ahora_chile().strftime("%Y-%m-%d %H:%M"), "historial": historial}
    finally:
        cursor.close()
        conn.close()

@app.put("/api/calendario")
def actualizar_calendario(datos: CalendarioConfigUpdate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE configuracion_calendario SET mes_cierre = %s, dia_cierre = %s WHERE id = 1", (datos.mes_cierre, datos.dia_cierre))
        registrar_log(cursor, user["nombre"], "CONFIG_CALENDARIO", f"Cierre escolar fijado: {datos.dia_cierre}/{datos.mes_cierre}")
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    verificar_automatizacion_calendario()
    return {"mensaje": "Calendario actualizado"}

@app.post("/api/calendario/simular-cierre")
def forzar_promocion_ahora(user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        anio = ahora_chile().year
        ejecutar_promocion_anual(cursor, anio, user["nombre"])
        conn.commit()
        return {"mensaje": f"Promoción anual {anio} ejecutada correctamente."}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/docente/panel")
def panel_docente(cursos: str, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    verificar_automatizacion_calendario()
    lista_cursos = [c.strip() for c in cursos.split(",") if c.strip()]
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        if "*" in lista_cursos or user.get("rol") == "maestro":
            cursor.execute("SELECT * FROM alumnos ORDER BY curso, nombre")
            alumnos = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM notas")
            notas = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM asistencia")
            asistencias = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM justificaciones ORDER BY leida ASC, fecha DESC")
            justificaciones = [dict(r) for r in cursor.fetchall()]
        else:
            cursor.execute("SELECT * FROM alumnos WHERE curso = ANY(%s) ORDER BY curso, nombre", (lista_cursos,))
            alumnos = [dict(r) for r in cursor.fetchall()]
            ruts = [a["rut"] for a in alumnos]
            notas = []
            asistencias = []
            if ruts:
                cursor.execute("SELECT * FROM notas WHERE rut = ANY(%s)", (ruts,))
                notas = [dict(r) for r in cursor.fetchall()]
                cursor.execute("SELECT * FROM asistencia WHERE rut = ANY(%s)", (ruts,))
                asistencias = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM justificaciones WHERE curso = ANY(%s) ORDER BY leida ASC, fecha DESC", (lista_cursos,))
            justificaciones = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT id, nombre FROM asignaturas WHERE activa = 1 ORDER BY id ASC")
        asignaturas = [dict(r) for r in cursor.fetchall()]
        return {
            "alumnos": alumnos,
            "notas": notas,
            "asistencias": asistencias,
            "justificaciones": justificaciones,
            "asignaturas": asignaturas,
            "hoy_chile": ahora_chile().strftime("%Y-%m-%d")
        }
    finally:
        cursor.close()
        conn.close()

@app.get("/api/alumnos")
def listar_alumnos(incluir_inactivos: bool = False):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        if incluir_inactivos:
            cursor.execute("SELECT * FROM alumnos ORDER BY curso, nombre")
        else:
            cursor.execute("SELECT * FROM alumnos WHERE activo = 1 ORDER BY curso, nombre")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()

@app.post("/api/alumnos")
def crear_alumno(alumno: AlumnoCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    rut_norm = normalizar_rut(alumno.rut)
    if not validar_rut_modulo11(rut_norm):
        raise HTTPException(status_code=400, detail="RUT inválido (dígito verificador no corresponde)")

    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT activo FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        existente = cursor.fetchone()
        if existente:
            if existente["activo"] == 0:
                cursor.execute("UPDATE alumnos SET nombre = %s, curso = %s, activo = 1 WHERE UPPER(REPLACE(rut, '.', '')) = %s", (alumno.nombre.strip().upper(), alumno.curso.strip(), rut_norm))
                registrar_log(cursor, user["nombre"], "REACTIVAR_ALUMNO", f"Reactivó alumno {rut_norm}")
                conn.commit()
                return {"mensaje": "Alumno reactivado", "rut": rut_norm}
            else:
                raise HTTPException(status_code=400, detail="Ese RUT ya está registrado y activo")

        cursor.execute("INSERT INTO alumnos (rut, nombre, curso, activo) VALUES (%s, %s, %s, 1)", (rut_norm, alumno.nombre.strip().upper(), alumno.curso.strip()))
        registrar_log(cursor, user["nombre"], "CREAR_ALUMNO", f"Matriculó a {alumno.nombre.strip().upper()} ({rut_norm}) en {alumno.curso}")
        conn.commit()
        return {"mensaje": "Alumno creado correctamente", "rut": rut_norm}
    finally:
        cursor.close()
        conn.close()

@app.put("/api/alumnos/{rut}")
def editar_alumno(rut: str, datos: AlumnoUpdate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("UPDATE alumnos SET nombre = %s, curso = %s WHERE UPPER(REPLACE(rut, '.', '')) = %s", (datos.nombre.strip().upper(), datos.curso.strip(), rut_norm))
        registrar_log(cursor, user["nombre"], "EDITAR_ALUMNO", f"Editó alumno {rut_norm} -> {datos.nombre.strip().upper()} ({datos.curso})")
        conn.commit()
        return {"mensaje": "Datos actualizados"}
    finally:
        cursor.close()
        conn.close()

@app.patch("/api/alumnos/{rut}/estado")
def cambiar_estado_alumno(rut: str, activo: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("UPDATE alumnos SET activo = %s WHERE UPPER(REPLACE(rut, '.', '')) = %s", (activo, rut_norm))
        registrar_log(cursor, user["nombre"], "ESTADO_ALUMNO", f"Cambió activo={activo} para {rut_norm}")
        conn.commit()
        return {"mensaje": "Estado actualizado"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/alumnos/{rut}")
def eliminar_alumno_definitivo(rut: str, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("DELETE FROM notas WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM observaciones WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM justificaciones WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM asistencia WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        registrar_log(cursor, user["nombre"], "ELIMINAR_ALUMNO", f"Eliminó definitivamente alumno {rut_norm}")
        conn.commit()
        return {"mensaje": "Eliminado definitivamente"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/notas")
def agregar_nota(nota: NotaCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    if not (1.0 <= nota.nota <= 7.0):
        raise HTTPException(status_code=400, detail="Nota debe estar entre 1.0 y 7.0")
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(nota.rut)
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO notas (rut, asignatura, nota, semestre, fecha, docente) VALUES (%s, %s, %s, %s, %s, %s)", (rut_norm, nota.asignatura, round(nota.nota, 1), nota.semestre, fecha, nota.docente))
        registrar_log(cursor, nota.docente, "NUEVA_NOTA", f"Nota {round(nota.nota, 1)} en {nota.asignatura} para {rut_norm}")
        conn.commit()
        return {"mensaje": "Nota registrada"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/notas/{nota_id}")
def eliminar_nota(nota_id: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM notas WHERE id = %s", (nota_id,))
        n = cursor.fetchone()
        cursor.execute("DELETE FROM notas WHERE id = %s", (nota_id,))
        if n:
            registrar_log(cursor, user["nombre"], "ELIMINAR_NOTA", f"Borró nota {n['nota']} ({n['asignatura']}) de {n['rut']}")
        conn.commit()
        return {"mensaje": "Nota eliminada"}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/portal/{rut}")
def datos_estudiante(rut: str):
    verificar_automatizacion_calendario()
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("SELECT * FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        alumno = cursor.fetchone()
        if not alumno:
            raise HTTPException(status_code=404, detail="Alumno no encontrado")
        curso = alumno["curso"]
        rut_real = alumno["rut"]
        cursor.execute("SELECT * FROM notas WHERE rut = %s", (rut_real,))
        notas = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM observaciones WHERE rut = %s ORDER BY fecha DESC", (rut_real,))
        observaciones = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM justificaciones WHERE rut = %s ORDER BY fecha DESC", (rut_real,))
        justificaciones = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM anuncios WHERE curso = %s OR curso = 'TODOS' ORDER BY fecha DESC", (curso,))
        anuncios = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM asistencia WHERE rut = %s ORDER BY fecha DESC", (rut_real,))
        asistencia = [dict(r) for r in cursor.fetchall()]
        return {
            "alumno": dict(alumno),
            "notas": notas,
            "observaciones": observaciones,
            "justificaciones": justificaciones,
            "anuncios": anuncios,
            "asistencia": asistencia,
            "fecha_emision": ahora_chile().strftime("%d/%m/%Y")
        }
    finally:
        cursor.close()
        conn.close()

@app.post("/api/observaciones")
def crear_observacion(obs: ObservacionCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(obs.rut)
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO observaciones (rut, tipo, texto, docente, fecha) VALUES (%s, %s, %s, %s, %s)", (rut_norm, obs.tipo, obs.texto, obs.docente, fecha))
        registrar_log(cursor, obs.docente, "OBSERVACION", f"Observación ({obs.tipo}) para {rut_norm}")
        conn.commit()
        return {"mensaje": "Observación guardada"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/justificaciones")
def crear_justificacion(just: JustificacionCreate):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO justificaciones (rut, curso, mensaje, fecha, leida) VALUES (%s, %s, %s, %s, 0)", (normalizar_rut(just.rut), just.curso, just.mensaje, fecha))
        conn.commit()
        return {"mensaje": "Justificación enviada"}
    finally:
        cursor.close()
        conn.close()

@app.patch("/api/justificaciones/{just_id}/leer")
def marcar_justificacion_leida(just_id: int, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE justificaciones SET leida = 1 WHERE id = %s", (just_id,))
        conn.commit()
        return {"mensaje": "Archivada"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/anuncios")
def crear_anuncio(anuncio: AnuncioCreate, user: dict = Depends(lambda h=Header(None): verificar_auth(h, ["docente", "maestro"]))):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO anuncios (curso, texto, docente, fecha) VALUES (%s, %s, %s, %s)", (anuncio.curso, anuncio.texto, anuncio.docente, fecha))
        registrar_log(cursor, anuncio.docente, "ANUNCIO", f"Publicó anuncio para {anuncio.curso}")
        conn.commit()
        return {"mensaje": "Anuncio publicado"}
    finally:
        cursor.close()
        conn.close()

app.mount("/", StaticFiles(directory=".", html=True), name="static")
