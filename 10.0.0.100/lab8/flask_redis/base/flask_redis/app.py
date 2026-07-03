import time
import socket
import os
import redis
from flask import Flask, make_response

DB_HOST = os.getenv('REDIS_HOST', 'redis')
MY_ENV = os.getenv('ENV', 'unknown')

app = Flask(__name__)
cache = redis.Redis(host=DB_HOST, port=6379)

def get_hit_count():
    retries = 5
    while True:
        try:
            return int(cache.get('hits') or 0)
        except redis.exceptions.ConnectionError as exc:
            if retries == 0:
                raise exc
            retries -= 1
            time.sleep(0.5)

def incr_hit_count() -> int:
    return cache.incr('hits')

@app.route('/')
def hello():
    incr_hit_count()
    count = get_hit_count()
    return (
        f'Hello World! I have been seen {count} times. '
        f'My name is: {socket.gethostname()} '
        f'My env: {MY_ENV} '
        f'Redis host: {DB_HOST}\n'
    )

@app.route('/metrics')
def metrics():
    response = make_response(
        f'''# HELP view_count Flask-Redis-App visit counter
# TYPE view_count counter
view_count{{service="Flask-Redis-App", env="{MY_ENV}"}} {get_hit_count()}
''', 200)
    response.mimetype = 'text/plain; charset=utf-8'
    return response
