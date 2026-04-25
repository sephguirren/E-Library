import os
import zipfile
from flask import Flask, render_template, request, jsonify, redirect, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pymysql
import pickle
from sklearn.metrics.pairwise import cosine_similarity

app = Flask(__name__)
app.secret_key = 'pink_reads_super_secret_key'

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'covers'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'chapters'), exist_ok=True)

# --- DATABASE CONNECTION ---
def get_db_connection():
    return pymysql.connect(
        host='localhost',
        user='root',
        password='',
        database='manhwa_library',
        cursorclass=pymysql.cursors.DictCursor
    )

# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

# ⚠️ RUN THIS ONCE TO FIX YOUR BOSS ACCOUNT PASSWORD!
@app.route('/setup_boss')
def setup_boss():
    hashed_pw = generate_password_hash('password123')
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE admins SET password_hash = %s WHERE username = 'boss'", (hashed_pw,))
    conn.commit()
    conn.close()
    return "✅ Boss account updated! Password is now: password123. Go to /login"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM admins WHERE username = %s", (username,))
            admin = cursor.fetchone()
        conn.close()

        if admin and check_password_hash(admin['password_hash'], password):
            session['admin_id'] = admin['id']
            session['username'] = admin['username']
            session['role'] = admin['role']
            return redirect('/admin')
        else:
            flash('Invalid username or password!')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ==========================================
# PUBLIC ROUTES
# ==========================================

@app.route('/')
def index():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT b.id, b.title, b.format, b.cover_image_url, a.name as author_name 
            FROM books b 
            JOIN authors a ON b.author_id = a.id
            ORDER BY b.created_at DESC
        """)
        books = cursor.fetchall()
    conn.close()
    return render_template('index.html', books=books)

@app.route('/read_book/<int:book_id>')
def read_book_start(book_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM chapters WHERE book_id = %s ORDER BY chapter_number ASC LIMIT 1", (book_id,))
        first_chapter = cursor.fetchone()
    conn.close()

    if first_chapter:
        return redirect(f"/read/{first_chapter['id']}")
    else:
        return "The admin hasn't uploaded any chapters for this book yet!", 404

@app.route('/read/<int:chapter_id>')
def read_chapter(chapter_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT c.*, b.title as book_title, b.format FROM chapters c JOIN books b ON c.book_id = b.id WHERE c.id = %s", (chapter_id,))
        chapter = cursor.fetchone()

        if not chapter:
            conn.close()
            return "Chapter not found", 404

        cursor.execute("SELECT id FROM chapters WHERE book_id = %s AND chapter_number < %s ORDER BY chapter_number DESC LIMIT 1", (chapter['book_id'], chapter['chapter_number']))
        prev_chapter = cursor.fetchone()

        cursor.execute("SELECT id FROM chapters WHERE book_id = %s AND chapter_number > %s ORDER BY chapter_number ASC LIMIT 1", (chapter['book_id'], chapter['chapter_number']))
        next_chapter = cursor.fetchone()

    conn.close()

    images = []
    if chapter['content_type'] == 'ZipFolder':
        folder_path = chapter['content_data'].strip('/') 
        if os.path.exists(folder_path):
            images = [f for f in os.listdir(folder_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
            images.sort() 

    return render_template('read.html', chapter=chapter, images=images, prev_chapter=prev_chapter, next_chapter=next_chapter)

# ==========================================
# PUBLISHER (ADMIN) ROUTES
# ==========================================

@app.route('/admin')
def admin_dashboard():
    # 1. Bouncer Check
    if 'admin_id' not in session: return redirect('/login')

    conn = get_db_connection()
    staff = []
    chart_labels = []
    chart_data = []
    total_chapters = 0
    
    with conn.cursor() as cursor:
        # 2. Get the Inventory
        cursor.execute("SELECT b.id, b.title, a.name AS author, b.format, b.cover_image_url FROM books b JOIN authors a ON b.author_id = a.id ORDER BY b.id DESC")
        books = cursor.fetchall()
        
        # 3. 📊 REAL GRAPH DATA: Count chapters per book for the top 6 books
        cursor.execute("""
            SELECT b.title, COUNT(c.id) as chapter_count 
            FROM books b 
            LEFT JOIN chapters c ON b.id = c.book_id 
            GROUP BY b.id 
            ORDER BY chapter_count DESC 
            LIMIT 6
        """)
        chapter_stats = cursor.fetchall()
        for stat in chapter_stats:
            chart_labels.append(stat['title'])
            chart_data.append(stat['chapter_count'])
            
        # 4. Get the exact number of total chapters for the stat card
        cursor.execute("SELECT COUNT(id) as total FROM chapters")
        total_chapters = cursor.fetchone()['total']
        
        # 5. Get staff list if Superadmin
        if session.get('role') == 'superadmin':
            cursor.execute("SELECT id, username, role FROM admins")
            staff = cursor.fetchall()
            
    conn.close()
    
    # Pass all the new math to the HTML!
    return render_template('admin.html', 
                           books=books, 
                           staff=staff, 
                           chart_labels=chart_labels, 
                           chart_data=chart_data,
                           total_chapters=total_chapters)

@app.route('/admin/add', methods=['POST'])
def add_book():
    if 'admin_id' not in session: return redirect('/login')

    title = request.form.get('title')
    author_name = request.form.get('author').strip()
    description = request.form.get('description')
    format_type = request.form.get('format')
    genres_input = request.form.get('genres') 
    
    cover_file = request.files.get('cover_image')
    cover_image_url = None
    if cover_file and cover_file.filename != '':
        filename = secure_filename(cover_file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'covers', filename)
        cover_file.save(save_path)
        cover_image_url = f"/static/uploads/covers/{filename}"

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM authors WHERE name = %s", (author_name,))
        author = cursor.fetchone()
        author_id = author['id'] if author else cursor.execute("INSERT INTO authors (name) VALUES (%s)", (author_name,)) or cursor.lastrowid

        cursor.execute("INSERT INTO books (title, author_id, description, format, cover_image_url) VALUES (%s, %s, %s, %s, %s)", (title, author_id, description, format_type, cover_image_url))
        book_id = cursor.lastrowid

        if genres_input:
            clean_genres = set([g.strip().title() for g in genres_input.split(',') if g.strip()])
            for g_name in clean_genres:
                cursor.execute("SELECT id FROM genres WHERE name = %s", (g_name,))
                genre = cursor.fetchone()
                genre_id = genre['id'] if genre else cursor.execute("INSERT INTO genres (name) VALUES (%s)", (g_name,)) or cursor.lastrowid
                cursor.execute("INSERT IGNORE INTO book_genres (book_id, genre_id) VALUES (%s, %s)", (book_id, genre_id))
    
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/add_chapter', methods=['POST'])
def add_chapter():
    if 'admin_id' not in session: return redirect('/login')

    book_id = request.form.get('book_id')
    chapter_number = request.form.get('chapter_number')
    content_type = request.form.get('content_type') 
    title = request.form.get('title', f"Chapter {chapter_number}")
    
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if content_type == 'ZipFolder':
            zip_file = request.files.get('chapter_zip')
            if zip_file and zip_file.filename.endswith('.zip'):
                folder_name = f"book_{book_id}_ch_{chapter_number}"
                extract_path = os.path.join(app.config['UPLOAD_FOLDER'], 'chapters', folder_name)
                os.makedirs(extract_path, exist_ok=True)
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)
                db_path = f"/static/uploads/chapters/{folder_name}/"
                cursor.execute("INSERT INTO chapters (book_id, chapter_number, title, content_type, content_data) VALUES (%s, %s, %s, %s, %s)", (book_id, chapter_number, title, content_type, db_path))
        elif content_type == 'Text':
            novel_text = request.form.get('novel_text')
            cursor.execute("INSERT INTO chapters (book_id, chapter_number, title, content_type, content_data) VALUES (%s, %s, %s, %s, %s)", (book_id, chapter_number, title, content_type, novel_text))
        conn.commit()
    except Exception as e:
        print(f"🔥 SERVER ERROR: {e}")
    finally:
        conn.close()
        
    return redirect('/admin')

@app.route('/admin/delete/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    if 'admin_id' not in session: return redirect('/login')
    if session.get('role') != 'superadmin': return "Access Denied: Superadmins only!", 403

    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/edit/<int:book_id>', methods=['POST'])
def edit_book(book_id):
    if 'admin_id' not in session: return redirect('/login')

    title = request.form.get('title')
    author_name = request.form.get('author').strip()
    format_type = request.form.get('format')
    
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM authors WHERE name = %s", (author_name,))
        author = cursor.fetchone()
        author_id = author['id'] if author else cursor.execute("INSERT INTO authors (name) VALUES (%s)", (author_name,)) or cursor.lastrowid
        cursor.execute("UPDATE books SET title = %s, author_id = %s, format = %s WHERE id = %s", (title, author_id, format_type, book_id))
        
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/admin/add_staff', methods=['POST'])
def add_staff():
    if 'admin_id' not in session: return redirect('/login')
    if session.get('role') != 'superadmin': return "Access Denied", 403
    
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    hashed_pw = generate_password_hash(password)
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO admins (username, password_hash, role) VALUES (%s, %s, %s)", (username, hashed_pw, role))
        conn.commit()
    except:
        pass 
    finally:
        conn.close()
    return redirect('/admin')

# ==========================================
# CHATBOT AI API
# ==========================================

@app.route('/api/chat', methods=['POST'])
def chat():
    user_message = request.json.get('message', '').lower()
    try:
        with open('chatbot_model.pkl', 'rb') as file:
            model_data = pickle.load(file)
            
        vectorizer = model_data['vectorizer']
        tfidf_matrix = model_data['matrix']
        books_df = model_data['books_df']

        user_vector = vectorizer.transform([user_message])
        similarities = cosine_similarity(user_vector, tfidf_matrix).flatten()
        top_indices = similarities.argsort()[-3:][::-1]
        
        recommendations = []
        for idx in top_indices:
            if similarities[idx] > 0.01: 
                book = books_df.iloc[idx]
                recommendations.append({
                    "id": int(book['id']),
                    "title": book['title'],
                    "author": book['author'],
                    "format": book['format'],
                    "cover_image_url": book['cover_image_url'] or "",
                    "match_score": round(similarities[idx] * 100, 1) 
                })

        if recommendations:
            reply = "I found some great matches based on what you are looking for! Check these out:"
        else:
            reply = "I'm sorry, I couldn't find any books that perfectly match that description. Try asking for a specific genre like 'Action Manhwa' or 'System Novel'!"

        return jsonify({"reply": reply, "recommendations": recommendations})

    except FileNotFoundError:
        return jsonify({"reply": "My AI brain is currently offline! The admin needs to run the training script.", "recommendations": []})

if __name__ == '__main__':
    app.run(debug=True)