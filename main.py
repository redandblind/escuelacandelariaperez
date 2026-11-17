import os
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
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

app = FastAPI(title="API Portal Escolar - Escuela Candelaria Pérez")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
db_pool: Optional[pool.SimpleConnectionPool] = None

ORDEN_CURSOS = [
    "1° Básico",
    "2° Básico",
    "3° Básico",
    "4° Básico",
    "5° Básico",
    "6° Básico",
    "7° Básico",
    "8° Básico"
]

def ahora_chile() -> datetime:
    return datetime.now(CHILE_TZ)

def siguiente_curso(curso_actual: str) -> Optional[str]:
    if curso_actual in ORDEN_CURSOS:
        idx = ORDEN_CURSOS.index(curso_actual)
        if idx + 1 < len(ORDEN_CURSOS):
            return ORDEN_CURSOS[idx + 1]
        return "EGRESADO"
    return curso_actual

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

def crear_token_docente(nombre: str, cursos: list) -> str:
    payload = {
        "rol": "docente",
        "nombre": nombre,
        "cursos": cursos,
        "exp": int((ahora_chile() + timedelta(hours=10)).timestamp())
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"

def verificar_docente_auth(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sesión docente requerida")
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
    if payload.get("rol") != "docente" or payload.get("exp", 0) < int(ahora_chile().timestamp()):
        raise HTTPException(status_code=401, detail="Sesión docente expirada, vuelve a ingresar")
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

def ejecutar_promocion_anual(cursor, anio: int):
    cursor.execute("SELECT * FROM alumnos WHERE activo = 1")
    alumnos = [dict(r) for r in cursor.fetchall()]

    for al in alumnos:
        rut = al["rut"]
        curso_actual = al["curso"]
        cursor.execute("SELECT asignatura, nota FROM notas WHERE rut = %s", (rut,))
        notas = [dict(r) for r in cursor.fetchall()]
        prom = calcular_promedio_alumno(notas)

        aprueba = (prom is None) or (prom >= 4.0)
        nuevo_curso = siguiente_curso(curso_actual) if aprueba else curso_actual
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

def ejecutar_limpieza_enero(cursor, anio_nuevo: int):
    cursor.execute("DELETE FROM notas")
    cursor.execute("DELETE FROM anuncios")
    cursor.execute("DELETE FROM justificaciones")
    cursor.execute(
        "UPDATE configuracion_calendario SET ultimo_anio_limpieza = %s WHERE id = 1",
        (anio_nuevo,)
    )

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
        print("Advertencia en automatizacion calendario:", e)
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
                activo INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS notas (
                id SERIAL PRIMARY KEY,
                rut TEXT NOT NULL,
                asignatura TEXT NOT NULL,
                nota REAL NOT NULL,
                semestre INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                docente TEXT NOT NULL
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
        print("Advertencia en init_db:", e)
    finally:
        cursor.close()
        conn.close()
    verificar_automatizacion_calendario()

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

class AlumnoUpdate(BaseModel):
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

class CalendarioConfigUpdate(BaseModel):
    mes_cierre: int
    dia_cierre: int

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "hora_chile": ahora_chile().strftime("%Y-%m-%d %H:%M:%S"),
        "db_configurada": bool(DATABASE_URL)
    }

@app.post("/api/login")
def login(data: LoginRequest):
    verificar_automatizacion_calendario()
    conn = DBConnection()
    cursor = conn.cursor()

    try:
        if data.rol == "docente":
            nombre_norm = data.identificador.strip().upper()
            cursor.execute("SELECT * FROM docentes WHERE UPPER(nombre) = %s AND activo = 1", (nombre_norm,))
            docente = cursor.fetchone()

            if not docente or not data.password or not pwd_context.verify(data.password, docente["password_hash"]):
                raise HTTPException(status_code=401, detail="Credenciales de docente inválidas")

            cursos_lista = [c.strip() for c in docente["cursos"].split(",") if c.strip()]
            token = crear_token_docente(docente["nombre"], cursos_lista)
            return {
                "rol": "docente",
                "nombre": docente["nombre"],
                "cursos": cursos_lista,
                "token": token
            }

        elif data.rol == "estudiante":
            rut_limpio = normalizar_rut(data.identificador)
            cursor.execute(
                "SELECT * FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s AND activo = 1",
                (rut_limpio,)
            )
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

@app.get("/api/calendario")
def obtener_calendario(docente: dict = Depends(verificar_docente_auth)):
    verificar_automatizacion_calendario()
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM configuracion_calendario WHERE id = 1")
        cfg = dict(cursor.fetchone())
        cursor.execute("SELECT * FROM historial_anual ORDER BY anio DESC, curso_anterior, nombre LIMIT 150")
        historial = [dict(r) for r in cursor.fetchall()]
        return {
            "config": cfg,
            "fecha_servidor": ahora_chile().strftime("%Y-%m-%d %H:%M"),
            "historial": historial
        }
    finally:
        cursor.close()
        conn.close()

@app.put("/api/calendario")
def actualizar_calendario(datos: CalendarioConfigUpdate, docente: dict = Depends(verificar_docente_auth)):
    if not (1 <= datos.mes_cierre <= 12 and 1 <= datos.dia_cierre <= 31):
        raise HTTPException(status_code=400, detail="Fecha inválida")
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE configuracion_calendario SET mes_cierre = %s, dia_cierre = %s WHERE id = 1",
            (datos.mes_cierre, datos.dia_cierre)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    verificar_automatizacion_calendario()
    return {"mensaje": "Calendario escolar actualizado"}

@app.post("/api/calendario/simular-cierre")
def forzar_promocion_ahora(docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        anio = ahora_chile().year
        cursor.execute("SELECT ultimo_anio_promocion FROM configuracion_calendario WHERE id = 1")
        cfg = cursor.fetchone()
        if cfg and cfg["ultimo_anio_promocion"] >= anio:
            raise HTTPException(status_code=400, detail=f"La promoción del año {anio} ya fue ejecutada anteriormente.")
        ejecutar_promocion_anual(cursor, anio)
        conn.commit()
        return {"mensaje": f"Promoción anual {anio} ejecutada: los alumnos con promedio >= 4,0 subieron de curso."}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/docente/panel")
def panel_docente(cursos: str, docente: dict = Depends(verificar_docente_auth)):
    verificar_automatizacion_calendario()
    lista_cursos = [c.strip() for c in cursos.split(",") if c.strip()]
    if not lista_cursos:
        return {"alumnos": [], "notas": [], "justificaciones": []}
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM alumnos WHERE curso = ANY(%s) ORDER BY curso, nombre",
            (lista_cursos,)
        )
        alumnos = [dict(r) for r in cursor.fetchall()]
        ruts = [a["rut"] for a in alumnos]

        notas = []
        if ruts:
            cursor.execute("SELECT * FROM notas WHERE rut = ANY(%s)", (ruts,))
            notas = [dict(r) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT * FROM justificaciones WHERE curso = ANY(%s) ORDER BY leida ASC, fecha DESC",
            (lista_cursos,)
        )
        justificaciones = [dict(r) for r in cursor.fetchall()]

        return {
            "alumnos": alumnos,
            "notas": notas,
            "justificaciones": justificaciones
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
def crear_alumno(alumno: AlumnoCreate, docente: dict = Depends(verificar_docente_auth)):
    rut_norm = normalizar_rut(alumno.rut)
    if not validar_rut_modulo11(rut_norm):
        raise HTTPException(status_code=400, detail="RUT inválido (dígito verificador no corresponde)")

    conn = DBConnection()
    cursor = conn.cursor()
    nombre_norm = alumno.nombre.strip().upper()
    curso_norm = alumno.curso.strip()
    try:
        cursor.execute("SELECT activo FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        existente = cursor.fetchone()
        if existente:
            if existente["activo"] == 0:
                cursor.execute(
                    "UPDATE alumnos SET nombre = %s, curso = %s, activo = 1 WHERE UPPER(REPLACE(rut, '.', '')) = %s",
                    (nombre_norm, curso_norm, rut_norm)
                )
                conn.commit()
                return {"mensaje": "Alumno reactivado y actualizado", "rut": rut_norm}
            else:
                raise HTTPException(status_code=400, detail="Ese RUT ya está registrado y activo")

        cursor.execute(
            "INSERT INTO alumnos (rut, nombre, curso, activo) VALUES (%s, %s, %s, 1)",
            (rut_norm, nombre_norm, curso_norm)
        )
        conn.commit()
        return {"mensaje": "Alumno creado correctamente", "rut": rut_norm}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al crear alumno: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.put("/api/alumnos/{rut}")
def editar_alumno(rut: str, datos: AlumnoUpdate, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute(
            "UPDATE alumnos SET nombre = %s, curso = %s WHERE UPPER(REPLACE(rut, '.', '')) = %s",
            (datos.nombre.strip().upper(), datos.curso.strip(), rut_norm)
        )
        conn.commit()
        return {"mensaje": "Datos del alumno actualizados"}
    finally:
        cursor.close()
        conn.close()

@app.patch("/api/alumnos/{rut}/estado")
def cambiar_estado_alumno(rut: str, activo: int, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("UPDATE alumnos SET activo = %s WHERE UPPER(REPLACE(rut, '.', '')) = %s", (activo, rut_norm))
        conn.commit()
        return {"mensaje": "Estado actualizado"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/alumnos/{rut}")
def eliminar_alumno_definitivo(rut: str, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    rut_norm = normalizar_rut(rut)
    try:
        cursor.execute("DELETE FROM notas WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM observaciones WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM justificaciones WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        cursor.execute("DELETE FROM alumnos WHERE UPPER(REPLACE(rut, '.', '')) = %s", (rut_norm,))
        conn.commit()
        return {"mensaje": "Alumno y registros eliminados definitivamente"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/notas")
def agregar_nota(nota: NotaCreate, docente: dict = Depends(verificar_docente_auth)):
    if not (1.0 <= nota.nota <= 7.0):
        raise HTTPException(status_code=400, detail="La nota debe estar entre 1.0 y 7.0")
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        fecha_actual = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO notas (rut, asignatura, nota, semestre, fecha, docente) VALUES (%s, %s, %s, %s, %s, %s)",
            (normalizar_rut(nota.rut), nota.asignatura, round(nota.nota, 1), nota.semestre, fecha_actual, docente["nombre"])
        )
        conn.commit()
        return {"mensaje": "Nota registrada"}
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/notas/{nota_id}")
def eliminar_nota(nota_id: int, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM notas WHERE id = %s", (nota_id,))
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

        cursor.execute("SELECT * FROM anuncios WHERE curso = %s ORDER BY fecha DESC", (curso,))
        anuncios = [dict(r) for r in cursor.fetchall()]

        return {
            "alumno": dict(alumno),
            "notas": notas,
            "observaciones": observaciones,
            "justificaciones": justificaciones,
            "anuncios": anuncios
        }
    finally:
        cursor.close()
        conn.close()

@app.post("/api/observaciones")
def crear_observacion(obs: ObservacionCreate, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO observaciones (rut, tipo, texto, docente, fecha) VALUES (%s, %s, %s, %s, %s)",
            (normalizar_rut(obs.rut), obs.tipo, obs.texto, docente["nombre"], fecha)
        )
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
        cursor.execute(
            "INSERT INTO justificaciones (rut, curso, mensaje, fecha, leida) VALUES (%s, %s, %s, %s, 0)",
            (normalizar_rut(just.rut), just.curso, just.mensaje, fecha)
        )
        conn.commit()
        return {"mensaje": "Justificación enviada"}
    finally:
        cursor.close()
        conn.close()

@app.patch("/api/justificaciones/{just_id}/leer")
def marcar_justificacion_leida(just_id: int, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE justificaciones SET leida = 1 WHERE id = %s", (just_id,))
        conn.commit()
        return {"mensaje": "Justificación archivada"}
    finally:
        cursor.close()
        conn.close()

@app.post("/api/anuncios")
def crear_anuncio(anuncio: AnuncioCreate, docente: dict = Depends(verificar_docente_auth)):
    conn = DBConnection()
    cursor = conn.cursor()
    try:
        fecha = ahora_chile().strftime("%Y-%m-%d %H:%M")
        cursor.execute(
            "INSERT INTO anuncios (curso, texto, docente, fecha) VALUES (%s, %s, %s, %s)",
            (anuncio.curso, anuncio.texto, docente["nombre"], fecha)
        )
        conn.commit()
        return {"mensaje": "Anuncio publicado"}
    finally:
        cursor.close()
        conn.close()

app.mount("/", StaticFiles(directory=".", html=True), name="static")
