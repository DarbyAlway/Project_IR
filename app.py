from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from elasticsearch import Elasticsearch
import os
from spellchecker import SpellChecker

# Initialize the Flask application and configure the database
app = Flask(__name__)
spell = SpellChecker()

# Configure SQLite database for users
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'  # SQLite database
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Disable modification tracking for efficiency
db = SQLAlchemy(app)

# Initialize Elasticsearch client
es = Elasticsearch(
    "https://localhost:9200",
    basic_auth=("elastic", os.getenv('ELASTIC_PASSWORD', 'Z_3O+lFyJPcXxPB+UvD-')),  # Using environment variable for security
    ca_certs=os.path.expanduser("~/http_ca.crt")  # Ensure the path is correct
)

# Define the User model to store user data
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)

# Create the database tables
with app.app_context():
    db.create_all()

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
            return redirect(url_for('mainpage'))
        else:
            return render_template('login.html', message="Invalid username or password.")
    
    return render_template('login.html')

@app.route('/mainpage')
def mainpage():
    return render_template('mainpage.html')

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    if query:
        # Perform spell correction on the query
        corrected_query = correct_spelling(query)

        # Now proceed with search (using corrected_query)
        response = es.search(index="recipes", body={
            "query": {
                "multi_match": {
                    "query": corrected_query,
                    "fields": ["Name", "Description", "RecipeInstructions"],
                }
            },
            "size": 1000
        })

        results = []
        seen_recipe_ids = set()  # Set to track unique RecipeIds

        for hit in response['hits']['hits']:
            recipe_id = hit['_source'].get('RecipeId')
            if recipe_id not in seen_recipe_ids:
                recipe = hit['_source']
                
                # Clean up the 'Images' field if it exists
                if 'Images' in recipe and recipe['Images']:
                    recipe['Images'] = recipe['Images'].replace('\\', '').strip('"')

                results.append(recipe)
                seen_recipe_ids.add(recipe_id)

        if results:
            return jsonify({
                "corrected_query": corrected_query,  # Return corrected query to the frontend
                "results": results
            })
        else:
            return jsonify({"message": "No results found"}), 404
    return jsonify({"message": "No query provided"}), 400

def correct_spelling(query):
    # Split the query into words
    words = query.split()

    # Correct each word
    corrected_words = [spell.correction(word) for word in words]

    # Join corrected words back into a string
    corrected_query = " ".join(corrected_words)
    
    return corrected_query

if __name__ == '__main__':
    app.run(debug=True)
