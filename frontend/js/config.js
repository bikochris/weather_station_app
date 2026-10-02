// Live Server uses the launcher's default FastAPI port; backend-served pages use their own origin.
const isLocalDevelopment = ["localhost", "127.0.0.1", ""].includes(
    window.location.hostname
);

window.WEATHER_API_URL = isLocalDevelopment && ["", "5500", "5501"].includes(window.location.port)
    ? "http://127.0.0.1:8000"
    : window.location.origin;
