# Admin UI — React + Vite dev server for local use (deploy optional).
FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install || true

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
