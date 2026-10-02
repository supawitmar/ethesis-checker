FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# รหัส commit ของ image นี้ (ไม่บังคับ) — .git ไม่อยู่ใน image จึงต้องส่งเข้ามาตอน build
# ไม่ส่งก็รันได้ปกติ แค่ท้ายหน้าเว็บกับ /health ขึ้นว่า unknown (Render ใส่ให้เองไม่ต้องส่ง)
#   docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t ethesis-checker .
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
