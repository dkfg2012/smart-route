FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY dashboard/frontend/package.json dashboard/frontend/tsconfig.json dashboard/frontend/vite.config.ts dashboard/frontend/index.html ./
COPY dashboard/frontend/src ./src
RUN npm config set registry https://registry.npmmirror.com && npm install && npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_DEFAULT_TIMEOUT=120
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dashboard .
COPY --from=frontend /app/frontend/dist /app/frontend/dist
COPY gateway /gateway
ENV PYTHONPATH=/gateway:/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
