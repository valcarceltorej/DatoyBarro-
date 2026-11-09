"""DatoyBarro - promesas del futbol (API-Football, plan gratis).
Uso: python Ingest.py all
Necesita la variable de entorno API_KEY.
"""
import json
import os
import sqlite3
import sys
import requests

DB = "datoybarro.db"
API = "https://v3.football.api-sports.io"
LIGAS = [140, 39, 135]
SEASON = 2024
MAX_EDAD = 21
MIN_MINUTOS = 450
RESERVA = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jugadores(
  id INTEGER PRIMARY KEY, nombre TEXT, edad INTEGER,
  nacimiento TEXT, nacionalidad TEXT, foto TEXT,
  posicion TEXT, equipo TEXT, liga TEXT);
CREATE TABLE IF NOT EXISTS stats(
  jugador_id INTEGER, temporada INTEGER, minutos INTEGER,
  partidos INTEGER, rating REAL, goles INTEGER,
  asistencias INTEGER, tiros INTEGER, tiros_puerta INTEGER,
  pases INTEGER, pases_clave INTEGER, entradas INTEGER,
  intercepciones INTEGER, duelos INTEGER,
  duelos_ganados INTEGER, regates INTEGER,
  regates_ok INTEGER, faltas_recibidas INTEGER,
  faltas_cometidas INTEGER,
  PRIMARY KEY(jugador_id, temporada));
CREATE TABLE IF NOT EXISTS indice(
  jugador_id INTEGER, temporada INTEGER, score REAL,
  PRIMARY KEY(jugador_id, temporada));
CREATE TABLE IF NOT EXISTS progreso(
  liga INTEGER, temporada INTEGER, pagina INTEGER,
  total INTEGER, PRIMARY KEY(liga, temporada));
"""


def db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c


def v(d, *claves):
    for k in claves:
        d = d.get(k) if isinstance(d, dict) else None
    return d if isinstance(d, (int, float)) else 0


def insertar(c, tabla, fila):
    cols = list(fila.keys())
    nombres = ",".join(cols)
    marcas = ",".join(":" + k for k in cols)
    sql = "INSERT OR REPLACE INTO %s(%s) VALUES(%s)"
    c.execute(sql % (tabla, nombres, marcas), fila)


def guardar(c, item, liga):
    p = item["player"]
    s = None
    for x in item["statistics"]:
        if x["league"]["id"] == liga:
            s = x
    if s is None or not p.get("age"):
        return
    if p["age"] > MAX_EDAD:
        return
    if v(s, "games", "minutes") < MIN_MINUTOS:
        return
    nac = (p.get("birth") or {}).get("date")
    insertar(c, "jugadores", {
        "id": p["id"], "nombre": p["name"], "edad": p["age"],
        "nacimiento": nac, "nacionalidad": p.get("nationality"),
        "foto": p.get("photo"),
        "posicion": s["games"].get("position"),
        "equipo": s["team"]["name"], "liga": s["league"]["name"]})
    rating = float(s["games"].get("rating") or 0)
    insertar(c, "stats", {
        "jugador_id": p["id"], "temporada": SEASON,
        "minutos": v(s, "games", "minutes"),
        "partidos": v(s, "games", "appearences"),
        "rating": rating,
        "goles": v(s, "goals", "total"),
        "asistencias": v(s, "goals", "assists"),
        "tiros": v(s, "shots", "total"),
        "tiros_puerta": v(s, "shots", "on"),
        "pases": v(s, "passes", "total"),
        "pases_clave": v(s, "passes", "key"),
        "entradas": v(s, "tackles", "total"),
        "intercepciones": v(s, "tackles", "interceptions"),
        "duelos": v(s, "duels", "total"),
        "duelos_ganados": v(s, "duels", "won"),
        "regates": v(s, "dribbles", "attempts"),
        "regates_ok": v(s, "dribbles", "success"),
        "faltas_recibidas": v(s, "fouls", "drawn"),
        "faltas_cometidas": v(s, "fouls", "committed")})


def load():
    h = {"x-apisports-key": os.environ["API_KEY"]}
    c = db()
    q = "SELECT pagina,total FROM progreso WHERE liga=? AND temporada=?"
    for liga in LIGAS:
        row = c.execute(q, (liga, SEASON)).fetchone()
        pagina, total = row if row else (1, 1)
        while pagina <= total:
            par = {"league": liga, "season": SEASON, "page": pagina}
            r = requests.get(API + "/players", headers=h,
                             params=par, timeout=30)
            cab = r.headers.get("x-ratelimit-requests-remaining", 100)
            quedan = int(cab) - 1
            d = r.json()
            if d.get("errors"):
                print("Error de la API:", d["errors"])
                c.commit()
                return
            total = d["paging"]["total"]
            for item in d["response"]:
                guardar(c, item, liga)
            pagina += 1
            c.execute("INSERT OR REPLACE INTO progreso VALUES(?,?,?,?)",
                      (liga, SEASON, pagina, total))
            c.commit()
            print("Liga", liga, "pagina", pagina - 1, "de", total,
                  "- quedan", quedan, "peticiones hoy")
            if quedan <= RESERVA:
                print("Cuota casi agotada: sigue manana.")
                return
        c.execute("UPDATE progreso SET pagina=1 WHERE liga=?", (liga,))
    c.commit()


def calcular_score(r):
    m90 = max(r["minutos"] / 90, 1)

    def cl(x):
        return max(0.0, min(1.0, x))

    ataque = cl((r["goles"] + r["asistencias"]) / m90 / 0.7)
    creacion = cl(r["pases_clave"] / m90 / 2.5)
    regate = cl(r["regates_ok"] / max(r["regates"], 1))
    defensa = cl((r["entradas"] + r["intercepciones"]) / m90 / 4)
    duelos = cl(r["duelos_ganados"] / max(r["duelos"], 1))
    rating = cl((r["rating"] - 6) / 1.5) if r["rating"] else 0
    pesos = {
        "Attacker": (.35, .15, .15, 0, .1, .25),
        "Midfielder": (.15, .25, .1, .15, .1, .25),
        "Defender": (.05, .05, .05, .35, .25, .25),
        "Goalkeeper": (0, 0, 0, 0, 0, 1)}
    w = pesos.get(r["posicion"], pesos["Midfielder"])
    valores = (ataque, creacion, regate, defensa, duelos, rating)
    tecnico = sum(a * b for a, b in zip(valores, w))
    juventud = cl((23 - r["edad"]) / 6)
    return round(100 * (0.7 * tecnico + 0.3 * juventud), 1)


def score():
    c = db()
    c.row_factory = sqlite3.Row
    sql = ("SELECT j.edad, j.posicion, s.* FROM stats s "
           "JOIN jugadores j ON j.id=s.jugador_id")
    for r in c.execute(sql).fetchall():
        punto = calcular_score(dict(r))
        c.execute("INSERT OR REPLACE INTO indice VALUES(?,?,?)",
                  (r["jugador_id"], r["temporada"], punto))
    c.commit()


def export(top=300):
    c = db()
    c.row_factory = sqlite3.Row
    sql = ("SELECT j.nombre, j.edad, j.posicion, j.equipo, j.liga, "
           "j.foto, s.*, i.score FROM indice i "
           "JOIN jugadores j ON j.id=i.jugador_id "
           "JOIN stats s ON s.jugador_id=i.jugador_id "
           "AND s.temporada=i.temporada "
           "ORDER BY i.score DESC LIMIT ?")
    filas = [dict(r) for r in c.execute(sql, (top,))]
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)
    print("data.json actualizado:", len(filas), "jugadores")


PASOS = {
    "load": [load],
    "score": [score],
    "export": [export],
    "all": [load, score, export]}

if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "all"
    for paso in PASOS.get(modo, []):
        paso()
