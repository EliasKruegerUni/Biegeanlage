"""Test-Sender für die Biegbert-Visualisierung.

Startet einen WebSocket-Server auf ws://localhost:8765 und bewegt die Achsen
mit Sinusbewegungen. index.html danach einfach öffnen (Doppelklick genügt).

    pip install websockets
    python ws_test_sender.py
"""
import asyncio, json, math, time
import websockets

async def handler(ws):
    print("Browser verbunden")
    async def receive():
        async for msg in ws:                      # hello / manuelle Slider-Bewegungen
            print("<-", msg[:200])
    rx = asyncio.create_task(receive())
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            values = {
                "Z1": 300 + 250 * math.sin(t * 0.3),
                "W": (t * 20) % 360 - 180,
                "X": 7 * math.sin(t * 0.8),
                "A": 32.5 * math.sin(t * 0.8),
                "NH1": 50 + 50 * math.sin(t * 1.0),
            }
            await ws.send(json.dumps({"type": "axes", "values": values}))
            await asyncio.sleep(1 / 30)
    except websockets.ConnectionClosed:
        print("Browser getrennt")
    finally:
        rx.cancel()

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        print("WebSocket-Server läuft auf ws://localhost:8765")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
