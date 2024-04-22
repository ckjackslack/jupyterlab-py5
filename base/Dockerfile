FROM python:3.10

WORKDIR /opt/app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-dev \
    libglib2.0-0 \
    default-jdk \
    make \
    autoconf \
    automake \
    libtool \
    flex \
    bison \
    bash \
    gdb \
    git \
    curl \
    wget \
    build-essential \
    gnupg \
    software-properties-common \
    nodejs \
    libffi-dev \
    libssl-dev \
    texlive \
    python3-poetry \
    xvfb \
    libxrender1 \
    libxtst6 \
    libxi6 \
    x11-apps \
    xauth \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

ENV DISPLAY=":0"
ENV JAVA_OPTS="-Xmx1024m -Xms1024m -XX:MaxPermSize=1024m -Djava.awt.headless=true"
ENV PATH="/root/.poetry/bin:${PATH}"

COPY pyproject.toml /opt/app
COPY requirements.txt /opt/app

RUN poetry config virtualenvs.create true \
 && poetry config virtualenvs.path "/venvs"

RUN poetry run pip install -r requirements.txt

RUN poetry run python -m py5jupyter.kernels.py5.install
RUN poetry run python -m py5jupyter.kernels.py5bot.install

EXPOSE 8888

ENTRYPOINT ["poetry", "run"]

CMD ["jupyter", "lab", "--ip='*'", "--port=8888", "--no-browser", "--allow-root"]
