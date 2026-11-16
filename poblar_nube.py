import os
import psycopg2
from passlib.context import CryptContext
from dotenv import load_dotenv
from main import init_db

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_HfWGOv9E2DQL@ep-restless-wave-be8pxmm9-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

init_db()

docentes = [
    ("MARTA QUEVEDO VILLALOBOS", "1° Básico, 2° Básico"),
    ("EDUARDO FUENTES REYES", "3° Básico, 4° Básico"),
    ("HÉCTOR GONZÁLEZ GAETE", "5° Básico"),
    ("EUGENIA BARROS BUSTAMANTE", "6° Básico"),
    ("KARINA SOTELO VALDÉS", "7° Básico"),
    ("ANA COFRÉ MUÑOZ", "8° Básico"),
]

alumnos = [
    ('27109816-2', 'BASTIAN ALONSO CAMPOS BAEZA', '1° Básico'),
    ('27102017-1', 'KHIARA EMILY CASTRO CARREÑO', '1° Básico'),
    ('26826120-6', 'IAN CALEB CONTRERAS GÓMEZ', '1° Básico'),
    ('26865752-5', 'COLOMBA ESTER DÍAZ VALVERDE', '1° Básico'),
    ('27204005-2', 'HANDERZON HELIEL HERNÁNDEZ ESQUIVEL', '1° Básico'),
    ('26792929-7', 'ESTEBAN JOSJUE NORAMBUENA CASTRO', '1° Básico'),
    ('26898511-5', 'SIMONEY ORTEGA BRIONES', '1° Básico'),
    ('26758311-0', 'DOMINIQUE AMELIA REICHENBACH VEGA', '1° Básico'),
    ('26729600-6', 'DANIEL ESTEBAN VILLEGAS VÁSQUEZ', '1° Básico'),
    ('27191114-9', 'IAN LUCIANO PÉREZ GONZÁLEZ', '1° Básico'),
    ('27009108-3', 'TRINIDAD ANDREA LARA QUIROZ', '1° Básico'),
    ('25796184-2', 'ELÍAS HEVER CASTRO CARREÑO', '2° Básico'),
    ('26402298-3', 'MATEO LUCIANO CORREA SEQUINI', '2° Básico'),
    ('26752038-0', 'LEMÓN ELUNEY FUENZALIDA ARAVENA', '2° Básico'),
    ('26659139-K', 'MATILDE TRINIDAD MUÑOZ ARAYA', '2° Básico'),
    ('26365506-K', 'SOFÍA IGNACIA PAILLAN ANCATÉN', '2° Básico'),
    ('26533453-9', 'AGUSTINA ALEJANDRA ROJAS GÓMEZ', '2° Básico'),
    ('26117713-7', 'DAVID ALEXIS RUIZ GONZÁLEZ', '2° Básico'),
    ('25919681-7', 'MATEO EXEQUIEL CAMPOS QUINTANA', '3° Básico'),
    ('25725971-4', 'JULIETA ISABELLA CANCINO ORMEÑO', '3° Básico'),
    ('26159496-K', 'AMARO LEÓN CIFUENTES CORREA', '3° Básico'),
    ('25864356-9', 'NEIDAN ANDRÉS HERNÁNDEZ ESQUIVEL', '3° Básico'),
    ('25928875-4', 'EMILIANO ALONSO JAQUE PEÑALOZA', '3° Básico'),
    ('26025635-1', 'FIORELLA IGNACIA MÉNDEZ ORTEGA', '3° Básico'),
    ('25935326-2', 'ISAAC ANDRÉS MORA MARTÍNEZ', '3° Básico'),
    ('25377391-K', 'AMARO ANDRÉS ARANEDA RAMÍREZ', '4° Básico'),
    ('25215012-9', 'JEREMIAS EDGAR CASTRO CARREÑO', '4° Básico'),
    ('25717568-5', 'MANUEL JESÚS ESQUIVEL GONZÁLEZ', '4° Básico'),
    ('25374420-0', 'MARÍA JOSÉ GÓMEZ GÓMEZ', '4° Básico'),
    ('25510730-5', 'AMANDA MARÍA ELIZABETH LUKSIC ASTUDILLO', '4° Básico'),
    ('25269634-2', 'JAVIER IGNACIO MARDONES IBACACHE', '4° Básico'),
    ('24961971-K', 'STEFAN ANTONIO REICHENBACH VEGA', '4° Básico'),
    ('25201656-2', 'FRANCO ANTONIO ACEVEDO ACUÑA', '5° Básico'),
    ('25054488-K', 'PAZ FRANCISCA BAEZA DÍAZ', '5° Básico'),
    ('25058732-5', 'JOSÉ HUMBERTO BUENO PAVEZ', '5° Básico'),
    ('25058760-0', 'JOSÉ OMAR BUENO PAVEZ', '5° Básico'),
    ('24533270-K', 'EDUARDO JAVIER CASTRO CARREÑO', '5° Básico'),
    ('25030936-8', 'MATÍAS NICOLÁS FAÚNDEZ SOTO', '5° Básico'),
    ('25003701-5', 'EMILYA JESÚS MARTINA GARRIDO HERRERA', '5° Básico'),
    ('25052798-5', 'ABHAY NITAY MARTÍNEZ ARAVENA', '5° Básico'),
    ('25284224-1', 'GERALDYNE ALEJANDRA MOYA CANCINO', '5° Básico'),
    ('25042294-6', 'MAGDALENA IGNACIA DEL CARMEN MUÑOZ ARAYA', '5° Básico'),
    ('25241982-9', 'BENJAMÍN ANTONIO OLIVARES MORALES', '5° Básico'),
    ('24954101-K', 'JAVIER IGNACIO ORELLANA VALDES', '5° Básico'),
    ('25106705-8', 'JULIETA VALERIA SANDOVAL JAQUE', '5° Básico'),
    ('25291352-1', 'MARTIN ALONSO SILVA ROA', '5° Básico'),
    ('25241359-6', 'AGUSTÍN ANTONIO VÁSQUEZ VÁSQUEZ', '5° Básico'),
    ('25212963-4', 'JOSÉ IGNACIO VERA MEDEL', '5° Básico'),
    ('25017516-7', 'MARTIN IGNACIO JARA BARRA', '5° Básico'),
    ('24934084-7', 'JOSEPH THOMAS BARBETT PINTO', '6° Básico'),
    ('24890180-2', 'MARIA ELENA BUENO PALMA', '6° Básico'),
    ('24356402-6', 'ALEXIS DAVID ALEJANDRO CANCINO PASTENES', '6° Básico'),
    ('24714647-4', 'EMILIA DEL CARMEN INOSTROZA TARAZONA', '6° Básico'),
    ('24893220-1', 'GABRIELA JESÚS MUÑOZ ORELLANA', '6° Básico'),
    ('24748067-6', 'VICTORIA ANABELLA SALGADO CARRASCO', '6° Básico'),
    ('24502047-3', 'DIEGO ALONZO VILLALOBOS VALDÉS', '6° Básico'),
    ('24502029-5', 'VICENTE IGNASIO VILLALOBOS VALDÉS', '6° Básico'),
    ('24906925-6', 'MATEO IGNACIO VIÑALS COFRE', '6° Básico'),
    ('24545483-K', 'ISAIASS ELIUDT ARANEDA RAMÍREZ', '7° Básico'),
    ('24503476-8', 'DANIEL ALEXIS CAMPOS YÁÑEZ', '7° Básico'),
    ('24434484-4', 'AGUSTÍN EFRAIN CASTRO CANALES', '7° Básico'),
    ('24389919-2', 'BAYTHIARE ALEXANDRA MÉNDEZ ORTEGA', '7° Básico'),
    ('24318562-9', 'SOFÍA AMANDA MUÑOZ ARAYA', '7° Básico'),
    ('23889737-8', 'ALEJANDRO ANÍBAL PINO ESQUIVEL', '7° Básico'),
    ('24490056-9', 'AARÓN ELÍAS SANTANDER CÉSPEDES', '7° Básico'),
    ('24339330-2', 'ANDY ANGEL URRUTIA GÓMEZ', '7° Básico'),
    ('24557543-2', 'MATEO ALONSO VALDÉS CABELLO', '7° Básico'),
    ('23977218-8', 'KIARA JULIANA ACEVEDO ACUÑA', '8° Básico'),
    ('24313880-9', 'DAMARIS ANDREA ALBORNOZ ZÁRATE', '8° Básico'),
    ('24075427-4', 'BENJAMÍN FRANCISCO ASTUDILLO CANALES', '8° Básico'),
    ('24022904-8', 'CRISTIAN YHEREMY CAMPOS BAEZA', '8° Básico'),
    ('24198053-7', 'TRIANA ANDREA CORREA ORMEÑO', '8° Básico'),
    ('24228200-0', 'ANA KARINA FAÚNDEZ SOTO', '8° Básico'),
    ('23754523-0', 'LUIS HUMBERTO FAÚNDEZ SOTO', '8° Básico'),
    ('24085862-2', 'CRISTÓBAL IGNACIO LOYOLA DÍAZ', '8° Básico'),
    ('24147417-8', 'YARIEL ANÍBAL MENA CANCINO', '8° Básico'),
    ('23948205-8', 'MANUEL ALEJANDRO ROJAS GÓMEZ', '8° Básico'),
    ('24160899-9', 'JESÚS ANTONIO SALGADO SALGADO', '8° Básico'),
    ('23728030-K', 'ROCÍO ANTONELLA SUAZO PIZARRO', '8° Básico'),
]

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

hash_clave = pwd_context.hash("candelaria16")
for nombre, cursos in docentes:
    cursor.execute("""
    INSERT INTO docentes (nombre, password_hash, cursos, activo)
    VALUES (%s, %s, %s, 1)
    ON CONFLICT (nombre) DO UPDATE SET
        password_hash = EXCLUDED.password_hash,
        cursos = EXCLUDED.cursos,
        activo = 1
    """, (nombre, hash_clave, cursos))

for rut, nombre, curso in alumnos:
    cursor.execute("""
    INSERT INTO alumnos (rut, nombre, curso, activo)
    VALUES (%s, %s, %s, 1)
    ON CONFLICT (rut) DO NOTHING
    """, (rut, nombre, curso))

conn.commit()
cursor.close()
conn.close()
print("Base de datos Neon poblada con 6 docentes y 79 alumnos.")
