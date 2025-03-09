from flask import Flask, render_template, request, redirect, url_for, jsonify,session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from elasticsearch import Elasticsearch
import os
from spellchecker import SpellChecker
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from collections import defaultdict
import re
from functools import wraps

# Initialize the Flask application and configure the database
app = Flask(__name__)
spell = SpellChecker()

app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'  # SQLite database
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Disable modification tracking for efficiency
db = SQLAlchemy(app)

# Initialize Elasticsearch client
es = Elasticsearch(
    "https://localhost:9200",
    basic_auth=("elastic", os.getenv('ELASTIC_PASSWORD', 'Z_3O+lFyJPcXxPB+UvD-')),  # Using environment variable for security
    ca_certs=os.path.expanduser("~/http_ca.crt")  # Ensure the path is correct
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))  # Redirect to login if the user is not logged in
        return f(*args, **kwargs)
    return decorated_function

# Define the User model to store user data
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

    folders = db.relationship('BookmarkFolder', backref='user', lazy=True)
class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey('bookmark_folder.id'), nullable=False)
    recipe_id = db.Column(db.Integer, nullable=False)
    recipe_name = db.Column(db.String(100), nullable=False)
    recipe_images = db.Column(db.String(500), nullable=False)
    recipe_description = db.Column(db.String(500), nullable=False)
    rating = db.Column(db.Integer, nullable=False)

    user = db.relationship('User', backref='bookmarks', lazy=True)

class BookmarkFolder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Relationship: One folder can have multiple bookmarks
    bookmarks = db.relationship('Bookmark', backref='folder', lazy=True)



@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Check if the username already exists in the database
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return render_template('register.html', message="Username already exists.")
        
        # Hash the password before saving it to the database
        hashed_password = generate_password_hash(password)
        
        # Save the new user to the database with the hashed password
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Check if the username exists in the database
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            # Store the user_id in the session
            session['user_id'] = user.id
            return redirect(url_for('mainpage'))
        else:
            return render_template('login.html', message="Invalid username or password.")
    
    return render_template('login.html')

@app.route('/mainpage')
@login_required
def mainpage():
    return render_template('mainpage.html')

recipe_data = []

@app.route('/search', methods=['GET'])
@login_required
def search():
    query = request.args.get('q', '')
    if not query:
        return jsonify({"message": "No query provided"}), 400

    # Perform spell correction
    corrected_query = correct_spelling(query)

    # Search in Elasticsearch using the corrected query
    response = es.search(index="recipes", body={
        "query": {
            "multi_match": {
                "query": corrected_query,
                "fields": ["Name", "Description", "RecipeInstructions"],
            }
        },
        "size": 50
    })

    results = []
    seen_recipe_ids = set()

    for hit in response['hits']['hits']:
        recipe = hit['_source']
        recipe_id = recipe.get('RecipeId')

        if recipe_id not in seen_recipe_ids:
            # Clean up image field if available
            if 'Images' in recipe and recipe['Images'] and recipe['Images'] != 'character(0)':
                recipe['Images'] = clean_image_url(recipe['Images'])
            seen_recipe_ids.add(recipe_id)
            results.append(recipe)

    return jsonify({
        "corrected_query": corrected_query,
        "results": results
    })

@app.route('/recipe_details', methods=['GET'])
@login_required
def recipe_details():
    # Get the recipe ID from the URL query string
    recipe_id = request.args.get('recipe_id')
    if not recipe_id:
        return jsonify({"message": "Recipe ID is missing"}), 400

    # Search for the recipe in Elasticsearch (or database, if applicable)
    response = es.search(index="recipes", body={
        "query": {
            "term": {
                "RecipeId": recipe_id  # Ensure this matches the field name in your index
            }
        },
        "size": 1
    })

    if response['hits']['total']['value'] == 0:
        return jsonify({"message": "Recipe not found"}), 404

    recipe = response['hits']['hits'][0]['_source']

    # Optionally, clean the image URL if necessary
    if 'Images' in recipe and recipe['Images']:
        recipe['Images'] = clean_image_url(recipe['Images'])
    print(recipe)
    return render_template('recipes_details.html', recipe=recipe)

@app.route('/bookmark/folders', methods=['GET'])
@login_required
def get_bookmark_folders():
    user_id = session.get('user_id')  # Get the user_id from the session
    if not user_id:
        return jsonify({"message": "User not logged in!"}), 401

    # Find the user
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "User not found!"}), 404

    # Fetch all bookmark folders for the user
    user_folders = BookmarkFolder.query.filter_by(user_id=user_id).all()
    folder_names = [folder.name for folder in user_folders]

    return jsonify({"folders": folder_names})


@app.route('/bookmark/existing', methods=['POST'])
@login_required
def save_to_existing():
    data = request.json
    user_id = session.get('user_id')  # Get the user_id from the session
    if not user_id:
        return jsonify({"message": "User not logged in!"}), 401
    
    folder_name = data['folder_name']
    recipe_id = data['recipe_id']
    recipe_name = data['recipe_name']
    Images = data['recipe_image']
    recipe_description = data['recipe_description']
    rating = data.get('rating')
    # Find the user
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "User not found!"}), 404

    # Find the folder by name and user_id
    folder = BookmarkFolder.query.filter_by(user_id=user_id, name=folder_name).first()
    if not folder:
        return jsonify({"message": f"Folder '{folder_name}' not found!"}), 404

    # Add recipe to existing folder
    bookmark = Bookmark(user_id=user_id, folder_id=folder.id, recipe_id=recipe_id, recipe_name=recipe_name, recipe_images=Images, recipe_description=recipe_description, rating=rating)
    db.session.add(bookmark)
    db.session.commit()

    return jsonify({"message": f"Recipe '{recipe_id}' saved to '{folder_name}' successfully!"})



@app.route('/bookmark/new', methods=['POST'])
@login_required
def save_to_new():
    data = request.json
    user_id = session.get('user_id')  # Get the user_id from the session
    if not user_id:
        return jsonify({"message": "User not logged in!"}), 401

    folder_name = data['folder_name']
    recipe_id = data['recipe_id']
    older_name = data['folder_name']
    recipe_id = data['recipe_id']
    recipe_name = data['recipe_name']
    Images = data['recipe_image']
    recipe_description = data['recipe_description']
    rating = data.get('rating')
    # Find the user
    user = User.query.get(user_id)
    if not user:
        return jsonify({"message": "User not found!"}), 404
    
    # Check if folder already exists
    existing_folder = BookmarkFolder.query.filter_by(user_id=user_id, name=folder_name).first()
    if existing_folder:
        return jsonify({"message": f"Folder '{folder_name}' already exists!"}), 400

    # Create a new folder
    new_folder = BookmarkFolder(user_id=user_id, name=folder_name)
    db.session.add(new_folder)
    db.session.commit()

    # Add the recipe to the new folder
    bookmark = Bookmark(user_id=user_id, folder_id=new_folder.id, recipe_id=recipe_id, recipe_name=recipe_name, recipe_images=Images, recipe_description=recipe_description,rating=rating)
    db.session.add(bookmark)
    db.session.commit()

    return jsonify({"message": f"New folder '{folder_name}' created and recipe '{recipe_id}' saved!"})

@app.route('/logout')
def logout():
    session.pop('user_id', None)  # Remove the user_id from the session
    return redirect(url_for('login'))





def clean_image_url(image_url):
    """
    Preprocess the image URL to always return a single image (the first image in the list).
    Handles different formats including c("url1", "url2", "url3") string format.
    """
    if image_url is None:
        return None
        
    if isinstance(image_url, list):
        # If the image_url is a list, return the first item in the list (cleaned)
        return image_url[0].strip('"') if image_url else None
    elif isinstance(image_url, str):
        # Handle the specific c("url1", "url2", "url3") format
        if image_url.startswith('c(') and ')' in image_url:
            # Extract the content inside the c() function
            content = image_url[2:-1].strip()
            
            # Find the first URL which should be in quotes
            match = re.search(r'"([^"]+)"', content)
            if match:
                return match.group(1)  # Return just the URL without quotes
                
        # Clean regular string URL
        return image_url.strip('"').strip()
    
    return None

def correct_spelling(query):
    # Split the query into words
    words = query.split()

    # Correct each word, using the original word if correction is None
    corrected_words = []
    for word in words:
        correction = spell.correction(word)
        # If no correction found, keep the original word
        if correction is None:
            corrected_words.append(word)
        else:
            corrected_words.append(correction)

    # Join corrected words back into a string
    corrected_query = " ".join(corrected_words)
    
    return corrected_query

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
