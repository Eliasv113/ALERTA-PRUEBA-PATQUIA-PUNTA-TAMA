import os
import json
import feedparser
import requests
import re
from datetime import datetime

# Diccionario con las localidades y sus coordenadas (Latitud, Longitud)
LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952)
}

RSS_URL = 'https://ssl.smn.gob.ar/CAP/AR.php'

# Configuración de Telegram
TELEGRAM_BOT_TOKEN = '8882322196:AAGYo3rXhptjmA4vzkt-hvkKZ6QT8b3T7ZM'
TELEGRAM_CHANNEL_ID = '-1004459131844'

HISTORIAL_FILE = 'historial_alerta_prueba_polygon.json'

def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return []
    return []

def guardar_historial(historial):
    with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)

def enviar_telegram_texto(mensaje):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'text': mensaje,
    }
    requests.post(url, json=payload)

def punto_en_poligono(latitud, longitud, poligono):
    x, y = longitud, latitud
    adentro = False
    
    n = len(poligono)
    if n == 0:
        return False
        
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
    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries:
        alerta_id = getattr(entry, 'id', getattr(entry, 'title', ''))
        
        if alerta_id in historial:
            continue
            
        titulo = getattr(entry, 'title', 'Alerta')
        descripcion_cruda = getattr(entry, 'description', '')
        
        limpio = re.sub(r'<[^>]+>', '\n', descripcion_cruda)
        limpio = re.sub(r'\n+', '\n', limpio)

        polygons_matches = re.findall(r'Polygon:\s*([\d\.\-,\s]+)', limpio, re.IGNORECASE)
        
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
            certeza = ""
            inicio = ""
            fin = ""
            descripcion = ""
            
            m_certeza = re.search(r'Certeza:\s*(.*)', limpio, re.IGNORECASE)
            if m_certeza: certeza = m_certeza.group(1).strip()
            
            m_inicio = re.search(r'Inicio:\s*(.*)', limpio, re.IGNORECASE)
            if m_inicio: inicio = formatear_fecha(m_inicio.group(1).strip())
            
            m_fin = re.search(r'Fin:\s*(.*)', limpio, re.IGNORECASE)
            if m_fin: fin = formatear_fecha(m_fin.group(1).strip())
            
            m_desc = re.search(r'Descripción:\s*(.*?)(?:Instrucciones:|Fuente:|Polygon:|$)', limpio, re.IGNORECASE | re.DOTALL)
            if m_desc: descripcion = m_desc.group(1).strip()
            
            pueblos_str = ", ".join(sorted(localidades_afectadas))
            
            mensaje = (
                f"🚨 {titulo}\n"
                f"📍 Zonas afectadas: {pueblos_str}\n\n"
                f"Certeza: {certeza}\n\n"
                f"Inicio: {inicio}\n"
                f"Fin: {fin}\n\n"
                f"{descripcion}\n\n"
                f"Fuente: https://www.smn.gob.ar/alertas"
            )
            
            enviar_telegram_texto(mensaje)
            
            historial.append(alerta_id)
            guardar_historial(historial)

if __name__ == '__main__':
    procesar_alertas()
