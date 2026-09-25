import argparse
import getpass

from mysql.connector import Error as MySQLError

from .database import get_connection
from .main import ensure_application_tables
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(
        description="Reset an application user's password."
    )
    parser.add_argument("username", help="Username of the account to reset")
    args = parser.parse_args()

    password = getpass.getpass("New temporary password: ")
    confirmation = getpass.getpass("Confirm temporary password: ")

    if len(password) < 8:
        raise SystemExit("Password must contain at least 8 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    connection = None
    cursor = None
    try:
        connection = get_connection()
        ensure_application_tables(connection)
        cursor = connection.cursor()
        cursor.execute(
            "SELECT user_id FROM users WHERE username = %s",
            (args.username.strip().lower(),),
        )
        user = cursor.fetchone()
        if user is None:
            raise SystemExit(f'User "{args.username}" was not found.')

        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s, must_change_password = TRUE
            WHERE user_id = %s
            """,
            (hash_password(password), user[0]),
        )
        cursor.execute("DELETE FROM user_sessions WHERE user_id = %s", (user[0],))
        connection.commit()
        print("Password reset. Sign in with the temporary password to choose a new one.")
    except MySQLError as exc:
        if connection is not None and connection.is_connected():
            connection.rollback()
        raise SystemExit(f"Database error: {exc}") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    main()
