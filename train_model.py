import pymysql
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
import pickle

def get_db_connection():
    return pymysql.connect(
        host='localhost',
        user='root',
        password='',
        database='manhwa_library'
        # 🔥 BUG FIXED: Removed DictCursor so pandas stops reading the column names!
    )

def train_model():
    print("🧠 Waking up the AI... connecting to database...")
    conn = get_db_connection()
    
    query = """
        SELECT b.id, b.title, b.description, b.format, b.cover_image_url, a.name as author, 
               GROUP_CONCAT(g.name SEPARATOR ' ') as genres
        FROM books b
        JOIN authors a ON b.author_id = a.id
        LEFT JOIN book_genres bg ON b.id = bg.book_id
        LEFT JOIN genres g ON bg.genre_id = g.id
        GROUP BY b.id
    """
    books = pd.read_sql(query, conn)
    conn.close()

    if books.empty:
        print("❌ No books found in the database. Please add some books first!")
        return

    # Clean the data safely
    books.fillna('', inplace=True)

    # Create the AI Soup
    books['ai_text'] = books['format'].astype(str) + " " + books['genres'].astype(str) + " " + books['title'].astype(str) + " " + books['author'].astype(str) + " " + books['description'].astype(str)

    print(f"\n📚 Reading {len(books)} books. Here is EXACTLY what the AI is memorizing:\n" + "-"*50)
    
    # X-RAY VISION
    for index, row in books.iterrows():
        print(f"📖 Book Title: {row['title']}")
        print(f"🧠 Words Learned: {row['ai_text']}\n")

    print("-" * 50)

    # Convert text to math
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(books['ai_text'])

    # Save the brain
    model_data = {
        'vectorizer': vectorizer,
        'matrix': tfidf_matrix,
        'books_df': books.drop(columns=['ai_text']) 
    }

    with open('chatbot_model.pkl', 'wb') as file:
        pickle.dump(model_data, file)

    print("✅ Training Complete! 'chatbot_model.pkl' has been generated.")

if __name__ == "__main__":
    train_model()