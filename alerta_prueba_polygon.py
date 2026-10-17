import os
import json
import requests
import re
from datetime import datetime

# Diccionario con las localidades y sus coordenadas (Latitud, Longitud)
LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952),
    "San Carlos de Bariloche": (-40.7500, -71.5000) # Coordenada de prueba
}

RSS_URL = 'https://ssl.smn.gob.ar/CAP/AR.php'

# Configuración de Telegram
TELEGRAM_BOT_TOKEN = '8882322196:AAGYo3rXhptjmA4vzkt-hvkKZ6QT8b3T7ZM'
TELEGRAM_CHANNEL_ID = '-1004459131844'
HISTORIAL_FILE = 'historial_alerta_prueba_polygon.json'

def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return []
    return []

def guardar_historial(historial):
    with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)

def enviar_telegram_texto(mensaje):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': TELEGRAM_CHANNEL_ID, 'text': mensaje})

def punto_en_poligono(latitud, longitud, poligono):
    x, y = longitud, latitud
    adentro = False
    n = len(poligono)
    if n == 0: return False
    p1_y, p1_x = poligono[0]
    for i in range(1, n + 1):
        p2_y, p2_x = poligono[i % n]
        if min(p1_y, p2_y) < y <= max(p1_y, p2_y):
            if x <= max(p1_x, p2_x):
                if p1_y != p2_y:
                    xinters = (y - p1_y) * (p2_x - p1_x) / (p2_y - p1_y) + p1_x
                if p1_x == p2_x or x <= xinters:
                    adentro = not adentro
        p1_x, p1_y = p2_x, p2_y
    return adentro

def formatear_fecha(fecha_str):
    try:
        dt = datetime.fromisoformat(fecha_str)
        return dt.strftime('%d/%m/%Y %H:%M')
    except:
        return fecha_str

def procesar_alertas():
    historial = cargar_historial()
    
    try:
        # Descargamos el XML crudo directamente del servidor del SMN
        respuesta = requests.get(RSS_URL, timeout=15)
        xml_text = respuesta.text
    except Exception as e:
        print(f"Error de conexión: {e}")
        return

    # Separamos el texto gigante en bloques por cada "alerta" (<entry>)
    entradas = re.split(r'<entry[^>]*>', xml_text, flags=re.IGNORECASE)
    print(f"Buscando alertas en el feed... Se encontraron {len(entradas)-1} alertas activas.")

    for entrada in entradas[1:]:
        # Extracción de ID y Título
        m_id = re.search(r'<id[^>]*>(.*?)</id>', entrada, re.IGNORECASE | re.DOTALL)
        alerta_id = m_id.group(1).strip() if m_id else "ID_DESCONOCIDO"

        m_titulo = re.search(r'<title[^>]*>(.*?)</title>', entrada, re.IGNORECASE | re.DOTALL)
        titulo = m_titulo.group(1).strip() if m_titulo else "Alerta"
        titulo = titulo.replace('<![CDATA[', '').replace(']]>', '')
        
        print(f"\n--- Analizando: {titulo} ---")
        
        if alerta_id in historial and alerta_id != "ID_DESCONOCIDO":
            print("Resultado: IGNORADA (Ya está en el historial).")
            continue

        # BUSCADOR DE POLÍGONOS A PRUEBA DE BALAS EN EL TEXTO CRUDO
        polygons_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', entrada, re.IGNORECASE | re.DOTALL)
        print(f"Polígonos encontrados: {len(polygons_matches)}")
        
        if not polygons_matches:
            print("Resultado: NO afecta a las localidades configuradas (Sin polígonos).")
            continue

        localidades_afectadas = set()
        
        for p_str in polygons_matches:
            puntos = []
            for par in p_str.split():
                partes = par.split(',')
                if len(partes) == 2:
                    try:
                        puntos.append((float(partes[0]), float(partes[1])))
                    except ValueError:
                        pass
            
            if puntos:
                for nombre_pueblo, (lat, lon) in LOCALIDADES.items():
                    if punto_en_poligono(lat, lon, puntos):
                        localidades_afectadas.add(nombre_pueblo)

        if localidades_afectadas:
            print(f"Resultado: ¡ALERTA POSITIVA para {localidades_afectadas}! Enviando a Telegram...")
            
            # Extraemos el resto de datos desde las etiquetas puras
            m_certeza = re.search(r'<[^>]*certainty[^>]*>(.*?)</[^>]*certainty>', entrada, re.IGNORECASE | re.DOTALL)
            certeza = m_certeza.group(1).strip() if m_certeza else "No especificada"
            
            m_inicio = re.search(r'<[^>]*effective[^>]*>(.*?)</[^>]*effective>', entrada, re.IGNORECASE | re.DOTALL)
            inicio_str = m_inicio.group(1).strip() if m_inicio else ""
            inicio = formatear_fecha(inicio_str) if inicio_str else "No especificado"
            
            m_fin = re.search(r'<[^>]*expires[^>]*>(.*?)</[^>]*expires>', entrada, re.IGNORECASE | re.DOTALL)
            fin_str = m_fin.group(1).strip() if m_fin else ""
            fin = formatear_fecha(fin_str) if fin_str else "No especificado"
            
            m_desc = re.search(r'<[^>]*description[^>]*>(.*?)</[^>]*description>', entrada, re.IGNORECASE | re.DOTALL)
            descripcion_cruda = m_desc.group(1).strip() if m_desc else ""
            # Limpiamos el texto para que se lea lindo en Telegram
            descripcion = descripcion_cruda.replace('<![CDATA[', '').replace(']]>', '')
            descripcion = re.sub(r'<[^>]+>', '\n', descripcion)
            descripcion = re.sub(r'\n+', '\n', descripcion).strip()
            
            pueblos_str = ", ".join(sorted(localidades_afectadas))
            
            mensaje = (
                f"🚨 {titulo}\n"
                f"📍 Zonas afectadas: {pueblos_str}\n\n"
                f"Certeza: {certeza}\n\n"
                f"Inicio: {inicio}\n"
                f"Fin: {fin}\n\n"
                f"{
