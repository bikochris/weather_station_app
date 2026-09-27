import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import mysql.connector
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
LOGIN_PATH = "/app/login.html"


def network_ip():
    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.connect(("8.8.8.8", 80))
        return connection.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        connection.close()


def quote_env(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class WeatherStationLauncher:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.root.title("Weather Station App Launcher")
        self.root.geometry("690x620")
        self.root.minsize(620, 570)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        current = dotenv_values(ENV_PATH)
        self.values = {
            "host": tk.StringVar(value=current.get("DB_HOST") or "localhost"),
            "port": tk.StringVar(value=current.get("DB_PORT") or "3306"),
            "user": tk.StringVar(value=current.get("DB_USER") or "weather_app"),
            "password": tk.StringVar(value=current.get("DB_PASSWORD") or ""),
            "name": tk.StringVar(value=current.get("DB_NAME") or "weather_db"),
            "web_port": tk.StringVar(value="8000"),
        }
        self.status = tk.StringVar(value="Ready")
        self.share_url = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)
        self.build_ui()
        self.refresh_url()

    def build_ui(self):
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        shell = ttk.Frame(self.root, padding=24)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Weather Station App", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="Database and network launcher").pack(anchor="w", pady=(2, 20))

        database = ttk.LabelFrame(shell, text="MariaDB connection", padding=16)
        database.pack(fill="x")
        self.add_field(database, "Server IP or hostname", "host", 0, 0)
        self.add_field(database, "Port", "port", 0, 1)
        self.add_field(database, "Database user", "user", 1, 0)
        password_frame = ttk.Frame(database)
        password_frame.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=7)
        ttk.Label(password_frame, text="Database password").pack(anchor="w")
        self.password_entry = ttk.Entry(password_frame, textvariable=self.values["password"], show="*")
        self.password_entry.pack(fill="x", pady=(5, 0))
        self.add_field(database, "Database name", "name", 2, 0)
        ttk.Checkbutton(
            database, text="Show password", variable=self.show_password,
            command=self.toggle_password,
        ).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(27, 7))
        database.columnconfigure(0, weight=1)
        database.columnconfigure(1, weight=1)
        database_actions = ttk.Frame(database)
        database_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(database_actions, text="Test database", command=self.test_database).pack(side="left")
        ttk.Button(database_actions, text="Save settings", command=self.save_settings).pack(side="left", padx=8)

        network = ttk.LabelFrame(shell, text="Web access", padding=16)
        network.pack(fill="x", pady=18)
        self.add_field(network, "Application port", "web_port", 0, 0)
        ttk.Label(network, text="Sharing address").grid(row=1, column=0, sticky="w", pady=(8, 4))
        url_row = ttk.Frame(network)
        url_row.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Entry(url_row, textvariable=self.share_url, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(url_row, text="Copy", command=self.copy_url).pack(side="left", padx=(8, 0))
        network.columnconfigure(0, weight=1)
        self.values["web_port"].trace_add("write", lambda *_: self.refresh_url())

        actions = ttk.Frame(shell)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Start app", command=self.start_app)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop_app, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Open app", command=self.open_app).pack(side="left")
        status_frame = ttk.LabelFrame(shell, text="Status", padding=14)
        status_frame.pack(fill="both", expand=True, pady=(18, 0))
        ttk.Label(status_frame, textvariable=self.status, wraplength=590).pack(anchor="w")

    def add_field(self, parent, label, key, row, column):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0), pady=7)
        ttk.Label(frame, text=label).pack(anchor="w")
        ttk.Entry(frame, textvariable=self.values[key]).pack(fill="x", pady=(5, 0))

    def toggle_password(self):
        self.password_entry.configure(show="" if self.show_password.get() else "*")

    def refresh_url(self):
        port = self.values["web_port"].get().strip() or "8000"
        self.share_url.set(f"http://{network_ip()}:{port}{LOGIN_PATH}")

    def database_settings(self):
        try:
            db_port = int(self.values["port"].get().strip())
            web_port = int(self.values["web_port"].get().strip())
        except ValueError as exc:
            raise ValueError("Database and application ports must be numbers") from exc
        if not 1 <= db_port <= 65535 or not 1 <= web_port <= 65535:
            raise ValueError("Ports must be between 1 and 65535")
        settings = {
            "DB_HOST": self.values["host"].get().strip(),
            "DB_PORT": str(db_port),
            "DB_USER": self.values["user"].get().strip(),
            "DB_PASSWORD": self.values["password"].get(),
            "DB_NAME": self.values["name"].get().strip(),
            "CORS_ORIGINS": "*",
        }
        if not all(settings[key] for key in ("DB_HOST", "DB_USER", "DB_NAME")):
            raise ValueError("Server, database user, and database name are required")
        return settings, web_port

    def write_env(self, settings):
        existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        output = []
        written = set()
        for line in existing:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if key in settings:
                output.append(f"{key}={quote_env(settings[key])}")
                written.add(key)
            else:
                output.append(line)
        for key, value in settings.items():
            if key not in written:
                output.append(f"{key}={quote_env(value)}")
        ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    def save_settings(self):
        try:
            settings, _ = self.database_settings()
            self.write_env(settings)
            self.status.set("Settings saved securely in .env")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Settings", str(exc))

    def test_database(self):
        try:
            settings, _ = self.database_settings()
        except ValueError as exc:
            messagebox.showerror("Database", str(exc))
            return
        self.status.set("Testing database connection...")

        def run_test():
            try:
                connection = mysql.connector.connect(
                    host=settings["DB_HOST"], port=int(settings["DB_PORT"]),
                    user=settings["DB_USER"], password=settings["DB_PASSWORD"],
                    database=settings["DB_NAME"], connection_timeout=8,
                )
                connection.close()
                message = "Database connection successful"
            except mysql.connector.Error as exc:
                message = f"Database connection failed: {exc}"
            self.root.after(0, self.status.set, message)

        threading.Thread(target=run_test, daemon=True).start()

    def port_is_available(self, port):
        check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            check.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
        finally:
            check.close()

    def start_app(self):
        if self.process and self.process.poll() is None:
            self.status.set("The application is already running")
            return
        try:
            settings, web_port = self.database_settings()
            self.write_env(settings)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Start app", str(exc))
            return
        if not self.port_is_available(web_port):
            self.status.set(f"Port {web_port} is already in use. The app may already be running.")
            return
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", str(web_port)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Starting application...")
        threading.Thread(target=self.wait_for_server, args=(web_port,), daemon=True).start()

    def wait_for_server(self, port):
        for _ in range(40):
            if self.process.poll() is not None:
                self.root.after(0, self.server_stopped, "The application stopped during startup")
                return
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(0.25)
            try:
                if connection.connect_ex(("127.0.0.1", port)) == 0:
                    self.root.after(0, self.status.set, f"Application running: {self.share_url.get()}")
                    return
            finally:
                connection.close()
            time.sleep(0.25)
        self.root.after(0, self.server_stopped, "The application did not start in time")

    def server_stopped(self, message="Application stopped"):
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set(message)

    def stop_app(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.server_stopped()

    def open_app(self):
        port = self.values["web_port"].get().strip() or "8000"
        webbrowser.open(f"http://127.0.0.1:{port}{LOGIN_PATH}")

    def copy_url(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.share_url.get())
        self.status.set("Sharing address copied")

    def close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Close launcher", "Stop the running application and close?"):
                return
            self.stop_app()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    WeatherStationLauncher(window)
    window.mainloop()
