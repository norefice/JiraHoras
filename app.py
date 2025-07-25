from flask import Flask, render_template, request, send_file
import requests
import os
import pandas as pd
from io import BytesIO
import werkzeug.urls
from datetime import datetime, timedelta

# Parchear el error si Flask intenta importar una función eliminada
if not hasattr(werkzeug.urls, "url_quote"):
    from urllib.parse import quote as url_quote
    werkzeug.urls.url_quote = url_quote
    
# Configuración de Jira
from config import JIRA_BASE_URL, JIRA_USER, JIRA_API_TOKEN, AUTH, HEADERS

# Crear app Flask
app = Flask(__name__)

# Función para obtener los sprints del proyecto
def get_sprints(board_id):
    sprints_url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/sprint"
    response = requests.get(sprints_url, headers=HEADERS, auth=AUTH)
    response.raise_for_status()
    
    sprints = response.json().get("values", [])
    return sprints

# Recupera todos los registros de tiempo asociados a un issue utilizando paginación.
def get_all_worklogs(issue_key):    
    worklogs = []
    start_at = 0
    while True:
        url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/worklog"
        params = {"startAt": start_at}
        response = requests.get(url, headers=HEADERS, auth=AUTH, params=params)
        response.raise_for_status()
        data = response.json()
        
        worklogs.extend(data.get("worklogs", []))
        if data.get("isLast", True):
            break
        start_at += data.get("maxResults", 0)
    
    return worklogs

# Función para obtener issues con worklogs para un sprint específico
def get_issues_with_worklogs(sprint_id):
    """
    Obtiene los issues de un sprint con sus fechas de inicio y fin.
    """
    # Obtén las fechas de inicio y fin del sprint
    sprint_url = f"{JIRA_BASE_URL}/rest/agile/1.0/sprint/{sprint_id}"
    sprint_response = requests.get(sprint_url, headers=HEADERS, auth=AUTH)
    sprint_response.raise_for_status()
    sprint_data = sprint_response.json()

    start_date = pd.to_datetime(sprint_data["startDate"])
    end_date = pd.to_datetime(sprint_data["endDate"])

    # Obtén los issues relacionados con el sprint
    url = f"{JIRA_BASE_URL}/rest/api/3/search"
    jql_query = f"sprint = {sprint_id} AND timespent IS NOT EMPTY"
    params = {
        "jql": jql_query,
        "fields": "summary,issuetype,timespent,worklog,customfield_10016",
        "expand": "worklog",
        "maxResults": 100
    }

    response = requests.get(url, headers=HEADERS, auth=AUTH, params=params)
    response.raise_for_status()
    issues = response.json().get("issues", [])

    return issues, start_date, end_date

# Función para procesar los worklogs y generar un reporte
def extract_worklog_details(issues, start_date, end_date):
    """
    Procesa los issues y obtiene los registros de trabajo dentro de un rango de fechas.
    """
    image_folder = "static/images"  # Ruta a la carpeta de imágenes de autores
    icon_folder = "static/icons"  # Ruta a la carpeta de íconos de tipos de tarea
    default_image = "default.jpg"  # Imagen por defecto para autores

    # Mapeo de tipos de tarea a íconos (ahora con Spike)
    task_icons = {
        "Tarea": "task.svg",
        "Error": "bug.svg",
        "Historia": "story.svg",
        "Spike": "spike.svg",
    }

    data = []
    for issue in issues:
        issue_key = issue["key"]
        issue_summary = issue["fields"]["summary"]
        issue_type = issue["fields"]["issuetype"]["name"]
        issue_icon = task_icons.get(issue_type, "default_icon.svg")
        story_points = issue["fields"].get("customfield_10016", "-")  # Obtén los Story Points
        worklogs = issue.get("fields", {}).get("worklog", {}).get("worklogs", [])

        for log in worklogs:
            log_date = pd.to_datetime(log["started"])
            if start_date <= log_date.date() <= end_date:  # Filtrar por rango de fechas
                author = log["author"]["displayName"]
                # Construir el nombre de archivo basado en el autor
                image_filename = f"{author.lower().replace(' ', '_')}.jpg"
                image_path = os.path.join(image_folder, image_filename)
                # Verificar si la imagen existe, si no, usar la imagen por defecto
                author_image = image_filename if os.path.exists(image_path) else default_image
                time_spent_seconds = log["timeSpentSeconds"]
                time_spent_hours = time_spent_seconds / 3600
                data.append({
                    "Issue Key": issue_key,
                    "Summary": issue_summary,
                    "Type": issue_type,
                    "TypeIcon": issue_icon,  # Ícono del tipo de tarea
                    "Story Points": story_points,
                    "Author": author,
                    "AuthorImage": author_image,  # Asegurarse de incluir AuthorImage
                    "Time Logged (h)": round(time_spent_hours, 2),
                    "Log Date": log_date.strftime("%Y-%m-%d")
                })
    return data

def get_issues_with_worklogs_by_date(start_date, end_date):
    """
    Obtiene los issues con worklogs en un rango de fechas.
    """
    # JQL para buscar issues con worklogs en el rango de fechas
    jql_query = f"worklogDate >= '{start_date.strftime('%Y-%m-%d')}' AND worklogDate <= '{end_date.strftime('%Y-%m-%d')}' AND timespent IS NOT EMPTY"
    url = f"{JIRA_BASE_URL}/rest/api/3/search"
    params = {
        "jql": jql_query,
        "fields": "summary,issuetype,timespent,worklog,customfield_10016",
        "expand": "worklog",
        "maxResults": 100
    }
    response = requests.get(url, headers=HEADERS, auth=AUTH, params=params)
    response.raise_for_status()
    issues = response.json().get("issues", [])
    return issues

# Ruta principal
@app.route("/", methods=["GET", "POST"])
def index():
    board_id = 129  # ID del tablero DEV
    
    sprints = get_sprints(board_id)
    # Ordenar sprints del más nuevo al más viejo
    sprints = sorted(sprints, key=lambda sprint: sprint.get("startDate", ""), reverse=True)
    
    worklog_details = []
    selected_sprint = None
    start_date = None
    end_date = None

    if request.method == "POST":
        selected_sprint = int(request.form.get("sprint")) if request.form.get("sprint") else None

        if selected_sprint:
            # Obtén los datos del sprint seleccionado
            issues, start_date, end_date = get_issues_with_worklogs(selected_sprint)
            worklog_details = extract_worklog_details(issues, start_date, end_date)
            worklog_details = sorted(worklog_details, key=lambda x: x["Log Date"])

    # Obtener lista única de autores y tipos de issue para los filtros
    authors = sorted(set(log["Author"] for log in worklog_details))
    issue_types = sorted(set(log["Type"] for log in worklog_details))

    return render_template(
        "index.html",
        sprints=sprints,
        worklog_details=worklog_details,
        authors=authors,
        issue_types=issue_types,  # Enviar tipos de issue a la plantilla
        selected_sprint=selected_sprint,
        start_date=start_date,
        end_date=end_date
    )
    
@app.route("/export", methods=["POST"])
def export_to_excel():
    try:
        # Obtén el ID del sprint desde el formulario
        sprint_id = request.form.get("sprint")
        if not sprint_id:
            return "No sprint selected", 400

        sprint_id = int(sprint_id)

        # Obtén los datos del sprint seleccionado
        issues, start_date, end_date = get_issues_with_worklogs(sprint_id)
        worklog_details = extract_worklog_details(issues, start_date, end_date)

        # Convierte los datos a un DataFrame de pandas
        df = pd.DataFrame(worklog_details)

        # Excluir las columnas no deseadas (AuthorImage, TypeIcon)
        columns_to_include = [
            "Issue Key",
            "Summary",
            "Type",
            "Story Points",
            "Author",
            "Log Date",
            "Time Logged (h)"
        ]
        df_filtered = df[columns_to_include]

        # Genera el archivo Excel
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_filtered.to_excel(writer, index=False, sheet_name="Worklogs")
        output.seek(0)

        # Devuelve el archivo Excel para descarga
        return send_file(
            output,
            as_attachment=True,
            download_name="worklogs_report.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

@app.route("/export_csv", methods=["POST"])
def export_to_csv():
    try:
        # Obtén el ID del sprint desde el formulario
        sprint_id = request.form.get("sprint")
        if not sprint_id:
            return "No sprint selected", 400

        sprint_id = int(sprint_id)

        # Obtén los datos del sprint seleccionado
        issues, start_date, end_date = get_issues_with_worklogs(sprint_id)
        worklog_details = extract_worklog_details(issues, start_date, end_date)

        # Convierte los datos a un DataFrame de pandas
        df = pd.DataFrame(worklog_details)

        # Excluir las columnas no deseadas (AuthorImage, TypeIcon)
        columns_to_include = [
            "Issue Key",
            "Summary",
            "Type",
            "Story Points",
            "Author",
            "Log Date",
            "Time Logged (h)"
        ]
        df_filtered = df[columns_to_include]

        # Genera el archivo CSV
        output = BytesIO()
        df_filtered.to_csv(output, index=False)
        output.seek(0)

        # Devuelve el archivo CSV para descarga
        return send_file(
            output,
            as_attachment=True,
            download_name="worklogs_report.csv",
            mimetype="text/csv"
        )
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

@app.route("/worklogs_por_fecha", methods=["GET", "POST"])
def worklogs_por_fecha():
    today = datetime.now().date()
    # Calcular los últimos 3 días hábiles
    days = 0
    last_days = []
    while len(last_days) < 3:
        if (today - timedelta(days=days)).weekday() < 5:  # Lunes a viernes
            last_days.append(today - timedelta(days=days))
        days += 1
    default_end = last_days[0]
    default_start = last_days[-1]
    worklog_details = []
    start_date = default_start
    end_date = default_end
    if request.method == "POST":
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        if start_date_str and end_date_str:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    issues = get_issues_with_worklogs_by_date(start_date, end_date)
    worklog_details = extract_worklog_details(issues, start_date, end_date)
    worklog_details = sorted(worklog_details, key=lambda x: x["Log Date"])
    authors = sorted(set(log["Author"] for log in worklog_details))
    issue_types = sorted(set(log["Type"] for log in worklog_details))
    return render_template(
        "worklogs_por_fecha.html",
        worklog_details=worklog_details,
        authors=authors,
        issue_types=issue_types,
        start_date=start_date,
        end_date=end_date
    )

@app.route("/export_worklogs_por_fecha", methods=["POST"])
def export_worklogs_por_fecha():
    try:
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        if not start_date_str or not end_date_str:
            return "Debe seleccionar un rango de fechas", 400
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        issues = get_issues_with_worklogs_by_date(start_date, end_date)
        worklog_details = extract_worklog_details(issues, start_date, end_date)
        df = pd.DataFrame(worklog_details)
        columns_to_include = [
            "Issue Key",
            "Summary",
            "Type",
            "Story Points",
            "Author",
            "Log Date",
            "Time Logged (h)"
        ]
        df_filtered = df[columns_to_include]
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_filtered.to_excel(writer, index=False, sheet_name="Worklogs")
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name="worklogs_report_fecha.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"An error occurred: {str(e)}", 500

# Ejecutar app
if __name__ == "__main__":
    app.run(debug=True)