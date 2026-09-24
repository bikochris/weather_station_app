// Local pages and Live Server use FastAPI on port 8000.
const isLocalDevelopment = ["localhost", "127.0.0.1", ""].includes(
    window.location.hostname
);

window.WEATHER_API_URL = isLocalDevelopment
    ? "http://127.0.0.1:8000"
    : window.location.origin;
