\# Лабораторная работа №8: Helm, Kompose, Kustomize



\*\*Курс:\*\* DevOps (СИТУ)

\*\*Тема:\*\* Управление Kubernetes-манифестами с помощью Helm, Kompose и Kustomize



\## Цель работы



На базе предыдущей лабораторной работы (k8s basics):



1\. Доработать приложение Flask — вынести имя Redis-сервера в переменную окружения.

2\. Создать Helm-chart из docker-compose проекта `prometheus-grafana` с помощью Kompose и развернуть его в Kubernetes.

3\. Настроить overlay-кастомизацию (Kustomize) для запуска приложения `flask + redis` в \*\*prod\*\* и \*\*dev\*\* окружениях на базе одного набора манифестов.



Работа выполнена на основе двух docker-compose проектов из предыдущих лабораторных:



| ВМ | Проект | Инструмент |

|---|---|---|

| `10.0.0.100` | Flask + Redis | Kustomize (base / dev / prod) |

| `10.0.0.110` | Prometheus + Grafana + Blackbox | Helm chart, сгенерированный через Kompose |



\## Архитектура



```mermaid

flowchart LR

&#x20;   subgraph VM1\["ВМ 10.0.0.100 (Minikube)"]

&#x20;       direction TB

&#x20;       Base\[base: flask.yml, redis.yml,\\nflask-service.yml, redis-service.yml]

&#x20;       Dev\[dev overlay\\nreplicas=2, REDIS\_HOST=dev-redis\\nport 54321]

&#x20;       Prod\[prod overlay\\nreplicas=5, REDIS\_HOST=prod-redis\\nport 54000]

&#x20;       Base --> Dev

&#x20;       Base --> Prod

&#x20;   end



&#x20;   subgraph VM2\["ВМ 10.0.0.110 (Minikube)"]

&#x20;       direction TB

&#x20;       Chart\[Helm chart promgra\\nиз prometheus\_grafana/compose.yaml]

&#x20;       Prometheus\[Prometheus]

&#x20;       Grafana\[Grafana :3000]

&#x20;       Blackbox\[Blackbox exporter]

&#x20;       Chart --> Prometheus

&#x20;       Chart --> Grafana

&#x20;       Chart --> Blackbox

&#x20;   end



&#x20;   Dev -- "/metrics :54321" --> Prometheus

&#x20;   Prod -- "/metrics :54000" --> Prometheus

&#x20;   Blackbox -- "http\_2xx probe" --> Dev

&#x20;   Blackbox -- "http\_2xx probe" --> Prod

&#x20;   Grafana -- "datasource" --> Prometheus

```



\## Структура репозитория



```

lab8/

├── flask\_redis/

│   ├── base/

│   │   ├── flask\_redis/

│   │   │   ├── app.py

│   │   │   ├── Dockerfile

│   │   │   └── requirements.txt

│   │   ├── flask-service.yml

│   │   ├── flask.yml

│   │   ├── redis-service.yml

│   │   ├── redis.yml

│   │   └── kustomization.yaml

│   ├── dev/

│   │   ├── kustomization.yaml

│   │   ├── service-patch.yaml

│   │   └── deployment-patch.yaml

│   └── prod/

│       ├── kustomization.yaml

│       ├── service-patch.yaml

│       └── deployment-patch.yaml

├── prometheus\_grafana/

│   ├── compose.yaml

│   ├── prometheus/

│   │   └── prometheus.yml

│   ├── grafana/

│   │   └── datasource.yml

│   └── promgra/                # Helm chart

│       ├── Chart.yaml

│       ├── values.yaml

│       └── templates/

│           ├── blackbox-deployment.yaml

│           ├── blackbox-service.yaml

│           ├── grafana-configmap.yaml

│           ├── grafana-deployment.yaml

│           ├── grafana-service.yaml

│           ├── prometheus-configmap.yaml

│           ├── prometheus-deployment.yaml

│           └── prometheus-service.yaml

└── README.md

```



\---



\## Часть 1. Доработка приложения: вынос имени сервера в переменную окружения



В коде приложения адрес Redis был захардкожен (`redis.Redis(host='redis', ...)`), что не позволяло переиспользовать один и тот же образ и манифесты для разных окружений через Kustomize. Имя хоста и метка окружения вынесены в переменные окружения `REDIS\_HOST` и `ENV`.



\*\*`flask\_redis/base/flask\_redis/app.py`\*\*



```python

import time

import socket

import os

import redis

from flask import Flask, make\_response



DB\_HOST = os.getenv('REDIS\_HOST', 'redis')

MY\_ENV = os.getenv('ENV', 'unknown')



app = Flask(\_\_name\_\_)

cache = redis.Redis(host=DB\_HOST, port=6379)



def get\_hit\_count():

&#x20;   retries = 5

&#x20;   while True:

&#x20;       try:

&#x20;           return int(cache.get('hits') or 0)

&#x20;       except redis.exceptions.ConnectionError as exc:

&#x20;           if retries == 0:

&#x20;               raise exc

&#x20;           retries -= 1

&#x20;           time.sleep(0.5)



def incr\_hit\_count() -> int:

&#x20;   return cache.incr('hits')



@app.route('/')

def hello():

&#x20;   incr\_hit\_count()

&#x20;   count = get\_hit\_count()

&#x20;   return (

&#x20;       f'Hello World! I have been seen {count} times. '

&#x20;       f'My name is: {socket.gethostname()} '

&#x20;       f'My env: {MY\_ENV} '

&#x20;       f'Redis host: {DB\_HOST}\\n'

&#x20;   )



@app.route('/metrics')

def metrics():

&#x20;   response = make\_response(

&#x20;       f'''# HELP view\_count Flask-Redis-App visit counter

\# TYPE view\_count counter

view\_count{{service="Flask-Redis-App", env="{MY\_ENV}"}} {get\_hit\_count()}

''', 200)

&#x20;   response.mimetype = 'text/plain; charset=utf-8'

&#x20;   return response

```



Образ пересобран внутри Minikube:



```bash

minikube image build -t flask:v2 ./flask\_redis/base/flask\_redis

minikube image ls | grep flask

```



В базовом манифесте деплоймента используется именно этот образ (`flask:v2`), а конкретное значение `REDIS\_HOST` подставляется патчем каждого окружения (см. ниже).



\---



\## Часть 2. Kustomize: dev и prod окружения для Flask + Redis



\### Base



Общие манифесты (`flask.yml`, `flask-service.yml`, `redis.yml`, `redis-service.yml`) вынесены в каталог `base/` и не содержат специфики окружения. Базовая кастомизация добавляет общий лейбл ко всем ресурсам:



\*\*`flask\_redis/base/kustomization.yaml`\*\*



```yaml

apiVersion: kustomize.config.k8s.io/v1beta1

kind: Kustomization



labels:

\- includeSelectors: true

&#x20; pairs:

&#x20;   app: devops-course-2025



resources:

\- flask-service.yml

\- flask.yml

\- redis-service.yml

\- redis.yml

```



Проверка без применения в кластер:



```bash

kubectl kustomize base

```



\### Dev overlay



\*\*`flask\_redis/dev/kustomization.yaml`\*\*



```yaml

apiVersion: kustomize.config.k8s.io/v1beta1

kind: Kustomization



resources:

\- ../base



namePrefix: dev-



labels:

\- includeSelectors: true

&#x20; pairs:

&#x20;   environment: dev



patches:

\- path: service-patch.yaml

&#x20; target:

&#x20;   labelSelector: 'svc=flask-front'

\- path: deployment-patch.yaml

```



`service-patch.yaml` меняет внешний порт сервиса на `54321`, `deployment-patch.yaml` задаёт 2 реплики и переменные окружения `REDIS\_HOST=dev-redis`, `ENV=DEVELOPMENT`.



\### Prod overlay



\*\*`flask\_redis/prod/kustomization.yaml`\*\* — аналогичная структура с `namePrefix: prod-`, лейблом `environment: prod`, портом `54000`, 5 репликами и `REDIS\_HOST=prod-redis`, `ENV=PRODUCTION`.



\### Применение и проверка



```bash

kubectl apply -k dev

kubectl apply -k prod



kubectl get deployments

kubectl get services

```



\*\*Результат:\*\*



| Ресурс | dev | prod |

|---|---|---|

| Deployment | `dev-flask-app` (2 реплики) | `prod-flask-app` (5 реплик) |

| Redis | `dev-redis` | `prod-redis` |

| Внешний порт | `54321` | `54000` |

| `ENV` | `DEVELOPMENT` | `PRODUCTION` |



Проверка приложения:



```bash

curl http://10.0.0.100:54321/

\# Hello World! ... My env: DEVELOPMENT Redis host: dev-redis



curl http://10.0.0.100:54000/

\# Hello World! ... My env: PRODUCTION Redis host: prod-redis

```



Оба окружения развёрнуты из \*\*одного и того же набора базовых манифестов\*\*, без каких-либо правок исходных YAML-файлов — вся специфика (имена ресурсов, лейблы, переменные окружения, число реплик, порты) вынесена в overlay-патчи Kustomize.



\---



\## Часть 3. Helm chart из docker-compose (Kompose): Prometheus + Grafana + Blackbox



\### Установка инструментов



```bash

\# Helm

curl -fsSL -o get\_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3

chmod 700 get\_helm.sh

./get\_helm.sh



\# Kompose

curl -L https://github.com/kubernetes/kompose/releases/download/v1.34.0/kompose-linux-amd64 -o kompose

chmod +x kompose

sudo mv ./kompose /usr/local/bin/kompose

```



\### Исходный проект



Проект `prometheus\_grafana` (`compose.yaml`, `prometheus/prometheus.yml`, `grafana/datasource.yml`) взят из предыдущей лабораторной работы (docker-compose, тема мониторинга).



\### Генерация chart



```bash

cd prometheus\_grafana

kompose convert --chart -f compose.yaml

mv compose promgra

```



Kompose сгенерировал базовую структуру Helm-chart (`Chart.yaml`, `values.yaml`, `templates/`), а содержимое конфигурационных файлов (`prometheus.yml`, `datasource.yml`) перенёс в `ConfigMap`, например:



```yaml

apiVersion: v1

kind: ConfigMap

metadata:

&#x20; labels:

&#x20;   io.kompose.service: grafana

&#x20; name: grafana-cm0

data:

&#x20; datasource.yml: |

&#x20;   apiVersion: 1

&#x20;   datasources:

&#x20;   - name: Prometheus

&#x20;     type: prometheus

&#x20;     url: http://prometheus:9090

&#x20;     isDefault: true

&#x20;     access: proxy

&#x20;     editable: true

```



\### Доработка chart



1\. Chart переименован в `promgra` (правка `Chart.yaml`, каталог переименован из `compose` в `promgra`).

2\. Сервис Grafana доработан вручную: тип `LoadBalancer` и `externalIPs`, чтобы сервис был доступен снаружи кластера на IP ВМ:



```yaml

apiVersion: v1

kind: Service

metadata:

&#x20; name: grafana

&#x20; labels:

&#x20;   io.kompose.service: grafana

spec:

&#x20; type: LoadBalancer

&#x20; ports:

&#x20;   - name: http

&#x20;     port: {{ .Values.EXTERNAL\_PORT }}

&#x20;     targetPort: 3000

&#x20; externalIPs:

&#x20;   - {{ .Values.EXTERNAL\_IP | quote }}

&#x20; selector:

&#x20;   io.kompose.service: grafana

```



3\. Значения параметризованы через `values.yaml`:



```yaml

EXTERNAL\_IP: "10.0.0.110"

EXTERNAL\_PORT: 3000

GF\_ADMIN\_PASSWORD: "HiGrafana"



FLASK\_DEV\_TARGET: "10.0.0.100:54321"

FLASK\_PROD\_TARGET: "10.0.0.100:54000"

```



`prometheus.yml` внутри ConfigMap также шаблонизирован — targets для scrape (Flask dev/prod окружения на ВМ `10.0.0.100`) и пароль администратора Grafana подставляются из `values.yaml`.



\### Проверка и упаковка



```bash

helm lint ./promgra

helm show values ./promgra

helm template promgra ./promgra



helm package ./promgra

```



\### Установка релиза



```bash

minikube tunnel --bind-address=10.0.0.110



helm upgrade --install promgra ./promgra-0.1.0.tgz \\

&#x20; -n monitoring --create-namespace



kubectl get pods -n monitoring

kubectl get services -n monitoring

```



\### Проверка работы



```bash

curl -I http://10.0.0.110:3000/login

```



Grafana доступна в браузере: `http://10.0.0.110:3000/login` (логин `admin`, пароль из `values.yaml`).

Datasource Prometheus подключён автоматически через ConfigMap.



\### Апгрейд релиза через values



Пример изменения значения без правки исходного chart:



```bash

helm upgrade promgra ./promgra -n monitoring \\

&#x20; --set EXTERNAL\_PORT=3456



helm get values promgra -n monitoring --all

```



\---



\## Итоги работы



\- Приложение Flask доработано: адрес Redis и метка окружения вынесены в переменные окружения (`REDIS\_HOST`, `ENV`), что убрало хардкод и позволило переиспользовать один образ/манифесты для разных сред.

\- Из docker-compose проекта `prometheus-grafana` с помощью \*\*Kompose\*\* сгенерирован и доработан \*\*Helm chart\*\* `promgra`; параметры (внешний IP/порт Grafana, пароль администратора, адреса целей Prometheus) вынесены в `values.yaml` и подставляются через шаблонизацию.

\- Из одного базового набора манифестов Flask + Redis с помощью \*\*Kustomize\*\* развёрнуты два независимых окружения — \*\*dev\*\* и \*\*prod\*\* — с разными именами ресурсов, лейблами, числом реплик, портами сервисов и переменными окружения, без изменения исходных YAML-файлов.

\- Итоговая конфигурация проверена сквозным сценарием: Prometheus (на `10.0.0.110`) собирает метрики с Flask dev/prod (`10.0.0.100:54321`, `10.0.0.100:54000`) и опрашивает их доступность через Blackbox exporter, а результаты визуализируются в Grafana.



\## Как воспроизвести



```bash

\# ВМ 10.0.0.100 — Flask/Redis

cd flask\_redis

kubectl apply -k dev

kubectl apply -k prod



\# ВМ 10.0.0.110 — Prometheus/Grafana/Blackbox

cd prometheus\_grafana

helm upgrade --install promgra ./promgra -n monitoring --create-namespace

```

