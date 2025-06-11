import streamlit as st
import sqlite3
import pandas as pd
import requests
import json
import os
from dotenv import load_dotenv
from openpyxl import load_workbook
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ---------------- Load Environment Variables ----------------
load_dotenv()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
OLLAMA_API_URL = 'http://172.25.60.20:11434/api/generate'

# ---------------- Upload Excel to SQLite ----------------
def upload_excel(file):
    try:
        wb = load_workbook(file)
        sheet = wb.active
        data = list(sheet.values)
        headers = [str(h).strip() for h in data[0]]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=headers)

        expected_columns = [
            'Name', 'CGPA', 'Location', 'Email',
            'Phone Number', 'Preferred Work Location', 'Specialization in Degree'
        ]
        missing_cols = [col for col in expected_columns if col not in df.columns]
        if missing_cols:
            return f"❌ Missing required columns: {', '.join(missing_cols)}", None

        df.rename(columns={
            'Name': 'name',
            'CGPA': 'cgpa',
            'Location': 'location',
            'Email': 'email',
            'Phone Number': 'phone_number',
            'Preferred Work Location': 'preferred_work_location',
            'Specialization in Degree': 'specialization'
        }, inplace=True)

        df.dropna(how='all', inplace=True)
        df = df[df.astype(str).ne('0').all(axis=1)]

        conn = sqlite3.connect("students.db")
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            cgpa REAL,
            location TEXT,
            email TEXT,
            phone_number TEXT,
            preferred_work_location TEXT,
            specialization TEXT
        )''')

        cursor.execute('DELETE FROM students')
        df.to_sql('students', conn, if_exists='append', index=False)
        conn.commit()
        conn.close()
        return f"✅ Successfully inserted {len(df)} records.", df
    except Exception as e:
        return f"❌ Error processing Excel file: {str(e)}", None

# ---------------- Retrieve Similar Examples ----------------
def retrieve_similar_examples(user_question, top_n=3):
    try:
        conn = sqlite3.connect('data/examples.db')
        cursor = conn.cursor()
        cursor.execute("SELECT question, query FROM examples")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        example_questions = [row[0] for row in rows]
        example_queries = [row[1] for row in rows]

        vectorizer = TfidfVectorizer().fit([user_question] + example_questions)
        question_vector = vectorizer.transform([user_question])
        examples_vector = vectorizer.transform(example_questions)

        similarities = cosine_similarity(question_vector, examples_vector)[0]
        top_indices = similarities.argsort()[-top_n:][::-1]

        return [(example_questions[i], example_queries[i]) for i in top_indices]
    except Exception:
        return []

# ---------------- Generate SQL Query ----------------
def generate_sql_query(question, examples_prompt=[]):
    prompt = """
You are an expert SQL assistant. Convert the user's natural language request into a valid SQLite SQL query.

Use this schema:
Table: students
Columns:
- name (TEXT)
- cgpa (REAL)
- location (TEXT)
- email (TEXT)
- phone_number (TEXT)
- preferred_work_location (TEXT)
- specialization (TEXT)

Allowed specializations (case-insensitive):
- Computer Science
- Electronics and Communication
- Mechanical Engineering
- Civil Engineering
- Electrical Engineering
- Information Technology
- Chemical Engineering
- Aerospace Engineering
- Biotechnology
- Environmental Engineering

⚠️ Guidelines:
- Use only SQL (no explanations or markdown)
- Use `LOWER(column_name)` for string comparisons.
- For highest CGPA: use a subquery like `cgpa = (SELECT MAX(cgpa) FROM students)`
- Return ONLY the valid SQL query.

"""

    if examples_prompt:
        prompt += "\nExamples:\n"
        for q, s in examples_prompt:
            prompt += f"Q: {q}\n{s.strip()}\n"

    prompt += f"\nUser Question: {question}"

    payload = {
        "model": "MFDoom/deepseek-r1-tool-calling:7b",
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_API_URL, headers={"Content-Type": "application/json"}, data=json.dumps(payload))
        response.raise_for_status()
        output = response.json().get('response', '').strip()

        output = output.replace("```sql", "").replace("```", "").strip()
        for line in output.splitlines():
            line = line.strip()
            if line.lower().startswith(("select", "insert", "update", "delete")):
                if "MAX(cgpa)" in line:
                    return "SELECT DISTINCT name FROM students WHERE cgpa = (SELECT MAX(cgpa) FROM students);"
                if line.lower().startswith("select") and "distinct" not in line.lower():
                    line = line.replace("SELECT", "SELECT DISTINCT", 1)
                return line.rstrip(";") + ";"
        return "❌ Failed to extract SQL from model response."
    except Exception as e:
        return f"❌ Ollama API error: {str(e)}"

# ---------------- Execute SQL ----------------
def execute_sql(sql):
    try:
        conn = sqlite3.connect("students.db")
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        conn.commit()
        conn.close()
        return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame()
    except sqlite3.Error as e:
        return f"❌ SQL Error: {str(e)}"

def is_write_query(sql):
    return sql.strip().split()[0].lower() in ['insert', 'update', 'delete', 'drop', 'alter', 'create', 'replace', 'truncate']

# ---------------- Streamlit App ----------------
st.set_page_config(page_title="Student DB ", layout="wide")
st.title("📊 Student Database Query Tool")

mode = st.radio("Select Mode:", ["📅 Upload Excel", "🧠 Ask a Question"])

if mode == "📅 Upload Excel":
    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx"])
    if uploaded_file:
        msg, df = upload_excel(uploaded_file)
        if df is not None:
            st.success(msg)
            st.dataframe(df)
        else:
            st.error(msg)

elif mode == "🧠 Ask a Question":
    user_question = st.text_input("Ask your question in natural language")
    if user_question:
        with st.spinner("🔍 Generating SQL query.."):        
            examples_used = retrieve_similar_examples(user_question)
            sql = generate_sql_query(user_question, examples_used)

        if examples_used:
            st.subheader("📚 Similar Examples Used (RAG):")
            for q, s in examples_used:
                st.markdown(f"**Q:** {q}")
                st.code(s, language="sql")

        st.subheader("🔧 Generated SQL Query:")
        st.code(sql, language="sql")

        if is_write_query(sql):
            admin_pass = st.text_input("Enter admin password:", type="password")
            if st.button("Run SQL") and admin_pass == ADMIN_PASSWORD:
                result = execute_sql(sql)
                if isinstance(result, str):
                    st.error(result)
                else:
                    st.subheader("📄 Query Results:")
                    st.dataframe(result)
        else:
            if st.button("Run SQL"):
                result = execute_sql(sql)
                if isinstance(result, str):
                    st.error(result)
                else:
                    st.subheader("📄 Query Results:")
                    st.dataframe(result)

                    # Option to save working example
                    if st.button("✅ Save this example for future use"):
                        try:
                            conn = sqlite3.connect("data/examples.db")
                            cur = conn.cursor()
                            cur.execute("""
                                CREATE TABLE IF NOT EXISTS examples (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    question TEXT,
                                    query TEXT
                                )
                            """)
                            cur.execute("SELECT * FROM examples WHERE question = ? AND query = ?", (user_question, sql))
                            if not cur.fetchone():
                                cur.execute("INSERT INTO examples (question, query) VALUES (?, ?)", (user_question, sql))
                                conn.commit()
                                st.success("✅ Saved to RAG examples.")
                            else:
                                st.info("ℹ️ Already saved.")
                            conn.close()
                        except Exception as e:
                            st.error(f"❌ Save failed: {str(e)}")
