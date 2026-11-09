"""DatoyBarro - promesas del fútbol con API-Football (plan gratuito: 100 peticiones/día).
Uso:  python ingest.py load | score | export | all
Requiere: pip install requests  y  export API_KEY=tu_clave
El script se detiene al agotar la cuota diaria y al día siguiente continúa donde lo dejó.
"""
import json, os, sqlite3, sys
import requests

DB = "datoybarro.db"
API = "https://v3.football.api-sports.io"
LIGAS = [140, 39, 135]       # ejemplo: LaLiga, Premier, Serie A (ids de API-Football)
SEASON = 2024                # el plan gratuito limita las temporadas: ajusta si da error
MAX_EDAD, MIN_MINUTOS = 21, 450
RESERVA = 3                  # peticiones que se dejan sin gastar

SCHEMA = """
CREATE TABLE IF NOT EXISTS jugadores(id INTEGER PRIMARY KEY, nombre TEXT, edad INTEGER,
  nacimiento TEXT, nacionalidad TEXT, foto TEXT, posicion TEXT, equipo TEXT, liga TEXT);
CREATE TABLE IF NOT EXISTS stats(jugador_id INTEGER, temporada INTEGER, minutos INTEGER, partidos INTEGER,
  rating REAL, goles INTEGER, asistencias INTEGER, tiros INTEGER, tiros_puerta INTEGER, pases INTEGER,
  pases_clave INTEGER, entradas INTEGER, intercepciones INTEGER, duelos INTEGER, duelos_ganados INTEGER,
  regates INTEGER, regates_ok INTEGER, faltas_recibidas INTEGER, faltas_cometidas INTEGER,
  PRIMARY KEY(jugador_id, temporada));
CREATE TABLE IF NOT EXISTS indice(jugador_id INTEGER, temporada INTEGER, score REAL,
  PRIMARY KEY(jugador_id, temporada));
CREATE TABLE IF NOT EXISTS progreso(liga INTEGER, temporada INTEGER, pagina INTEGER, total INTEGER,
  PRIMARY KEY(liga, temporada));
"""

def db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c

def n(v):
    return v if isinstance(v, (int, float)) else 0

def load():
    h = {"x-apisports-key": os.environ["API_KEY"]}
    c = db()
    for liga in LIGAS:
        row = c.execute("SELECT pagina,total FROM progreso WHERE liga=? AND temporada=?", (liga, SEASON)).fetchone()
        pagina, total = row if row else (1, 1)
        while pagina <= total:
            r = requests.get(f"{API}/players", headers=h, timeout=30,
                             params={"league": liga, "season": SEASON, "page": pagina})
            restantes = int(r.headers.get("x-ratelimit-requests-remaining", 100))
            d = r.json()
            if d.get("errors"):
                print("Error de la API:", d["errors"]); c.commit(); return
            total = d["paging"]["total"]
            for it in d["response"]:
                p = it["player"]
                s = next((x for x in it["statistics"] if x["league"]["id"] == liga), None)
                if not s or (p.get("age") or 99) > MAX_EDAD or n(s["games"]["minutes"]) < MIN_MINUTOS:
                    continue
                g, sh, ps, tk, du, dr, fl = s["goals"], s["shots"], s["passes"], s["tackles"], s["duels"], s["dribbles"], s["fouls"]
                c.execute("INSERT OR REPLACE INTO jugadores VALUES(?,?,?,?,?,?,?,?,?)",
                          (p["id"], p["name"], p["age"], p["birth"]["date"], p["nationality"], p["photo"],
                           s["games"]["position"], s["team"]["name"], s["league"]["name"]))
                c.execute("INSERT OR REPLACE INTO stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (p["id"], SEASON, n(s["games"]["minutes"]), n(s["games"]["appearences"]),
                           float(s["games"]["rating"] or 0), n(g["total"]), n(g["assists"]), n(sh["total"]), n(sh["on"]),
                           n(ps["total"]), n(ps["key"]), n(tk["total"]), n(tk["interceptions"]), n(du["total"]),
                           n(du["won"]), n(dr["attempts"]), n(dr["success"]), n(fl["drawn"]), n(fl["committed"])))
            pagina += 1
            c.execute("INSERT OR REPLACE INTO progreso VALUES(?,?,?,?)", (liga, SEASON, pagina, total))
            c.commit()
            print(f"Liga {liga}: página {pagina - 1}/{total}, quedan {restantes - 1} peticiones hoy")
            if restantes - 1 <= RESERVA:
                print("Cuota diaria casi agotada: continuará mañana."); return
        c.execute("UPDATE progreso SET pagina=1 WHERE liga=? AND temporada=?", (liga, SEASON))  # liga completa
    c.commit()

def calcular_score(r):
    """r: dict con edad, posicion y stats. Todo normalizado a 0-1 y ponderado por posición."""
    m90 = max(r["minutos"] / 90, 1)
    cl = lambda v: max(0.0, min(1.0, v))
    ataque = cl((r["goles"] + r["asistencias"]) / m90 / 0.7)
    creacion = cl(r["pases_clave"] / m90 / 2.5)
    regate = cl(r["regates_ok"] / max(r["regates"], 1))
    defensa = cl((r["entradas"] + r["intercepciones"]) / m90 / 4)
    duelos = cl(r["duelos_ganados"] / max(r["duelos"], 1))
    rating = cl((r["rating"] - 6) / 1.5) if r["rating"] else 0
    pesos = {"Attacker": (.35, .15, .15, 0, .1, .25), "Midfielder": (.15, .25, .1, .15, .1, .25),
             "Defender": (.05, .05, .05, .35, .25, .25), "Goalkeeper": (0, 0, 0, 0, 0, 1)}
    w = pesos.get(r["posicion"], pesos["Midfielder"])
    tecnico = sum(a * b for a, b in zip((ataq
