FROM python:3.12-slim    
WORKDIR /app    
COPY requirements.txt .    
RUN pip install --no-cache-dir -r requirements.txt    
COPY . .    
ENV PEAWORK_HOST=0.0.0.0 PEAWORK_PORT=8090    
EXPOSE 8090    
CMD ["python", "run.py"]
