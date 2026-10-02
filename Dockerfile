FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir Flask PyJWT scikit-learn Werkzeug gunicorn
COPY . .
ENV DB_PATH=/srv/data/app.db PORT=5000
EXPOSE 5000
CMD ["gunicorn", "-w", "2", "--threads", "4", "-b", "0.0.0.0:5000", "app.app:create_app()"]
