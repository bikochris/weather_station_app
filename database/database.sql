import mariadb


def get_connection():
    connection = mariadb.connect(
        host="127.0.0.1",
        port=3308,
        user="root",
        password="root",
        database="weather_db"
    )

    return connection