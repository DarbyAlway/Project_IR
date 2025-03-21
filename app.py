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
import pandas as pd
import random
from sklearn.preprocessing import LabelEncoder
from sklearn.impute import SimpleImputer
import pickle
from collections import deque
import joblib
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
    folder_id = db.Column(db.Integer, db.ForeignKey('bookmark_folder.id'), nullable=True)  # Set nullable=True
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

    # Initialize search history in session if not present as a list
    if 'search_history' not in session:
        session['search_history'] = []  # Using list instead of deque

    # Add the current query to the beginning of the list
    session['search_history'].insert(0, query)

    # Limit the search history to the most recent 10 entries
    session['search_history'] = session['search_history'][:10]

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

    # Retrieve search history from session (latest 10 searches)
    search_history = list(session['search_history'])

    return jsonify({
        "corrected_query": corrected_query,
        "results": results,
        "search_history": search_history
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

@app.route('/show_bookmark', methods=['GET'])
def get_bookmarks():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    folders = BookmarkFolder.query.filter_by(user_id=user_id).all()
    
    # Get bookmarks for each folder and format them
    formatted_bookmarks = [
        {
            "recipe_id": bookmark.recipe_id,
            "recipe_name": bookmark.recipe_name,
            "recipe_image": bookmark.recipe_images,
            "recipe_description": bookmark.recipe_description,
            "rating": bookmark.rating,
            "folder_name": bookmark.folder.name if bookmark.folder else "Uncategorized"
        }
        for folder in folders
        for bookmark in folder.bookmarks
    ]
    return render_template('bookmark.html', folders=folders, bookmarks=formatted_bookmarks)

df = pd.read_csv('full_recipes.csv')
encoder = joblib.load('encoder.pkl')  # Load the pre-trained encoder for RecipeCategory
df['RecipeCategory'] = encoder.fit_transform(df['RecipeCategory'])  # Use the encoder to transform the RecipeCategory

@app.route('/bookmark_detail', methods=['GET'])
def bookmark_detail():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    folder_id = request.args.get('folder_id')
    if not folder_id:
        return "Folder not found", 404

    folder = BookmarkFolder.query.filter_by(id=folder_id, user_id=user_id).first()
    if not folder:
        return "Folder not found", 404

    # Fetch the bookmarks from the folder (using the database query)
    bookmarks = Bookmark.query.filter_by(folder_id=folder.id).all()

    # Prepare list of recipe_ids to search in Elasticsearch
    recipe_ids = [bookmark.recipe_id for bookmark in bookmarks]

    # Fetch bookmark details from Elasticsearch based on recipe_id
    es_results = []
    if recipe_ids:
        es_query = {
            "query": {
                "terms": {
                    "RecipeId": recipe_ids
                }
            }
        }
        es_response = es.search(index="recipes", body=es_query)

        # Parse Elasticsearch results and store in es_results
        for hit in es_response['hits']['hits']:
            es_results.append(hit['_source'])

    # Assuming `data` is a DataFrame containing all recipes with features from Elasticsearch or other sources
    # Define features to be used for prediction
    features = ['RecipeId', 'AggregatedRating', 'ReviewCount', 'Calories', 'FatContent', 
                'SaturatedFatContent', 'CholesterolContent', 'SodiumContent', 'CarbohydrateContent', 
                'FiberContent', 'SugarContent', 'ProteinContent', 'RecipeServings', 'RecipeCategory']

    # Assuming the trained LightGBM model, scaler, and encoder are loaded here
    model = joblib.load('lgbm_regressor.pkl')  # Load your pre-trained model
    scaler = joblib.load('scaler.pkl')  # Load the pre-trained scaler used in feature scaling

    # New user data (e.g., a bookmarked recipe)
    user_bookmarked_data = pd.DataFrame({
        'UserId': [user_id] * len(bookmarks),  # Repeat the user_id for each bookmark
        'RecipeId': [bookmark.recipe_id for bookmark in bookmarks]
    })

    # List of RecipeIds that the user has already bookmarked
    bookmarked_recipes = user_bookmarked_data[user_bookmarked_data['UserId'] == user_id]['RecipeId'].tolist()

    # Step 1: Encode the RecipeCategory using the encoder

    # Step 2: Scale the old data (to make predictions for the top 10 recommendations)
    X_scaled_old = scaler.transform(df[features])  # Scale the features of old data
    # Predict ratings for all old recipes
    predicted_ratings_old = model.predict(X_scaled_old)

    # Add predicted ratings to the DataFrame
    df['PredictedRating'] = predicted_ratings_old

    # Step 3: Exclude the recipes the user has already bookmarked
    data_to_recommend = df[~df['RecipeId'].isin(bookmarked_recipes)]

    # Step 4: Sort by predicted ratings in descending order to recommend the best ones
    recommended_recipes = data_to_recommend.sort_values('PredictedRating', ascending=False)

    # Step 5: Get top 10 recommended recipes for the user
    top_10_recipes = recommended_recipes[['RecipeId', 'PredictedRating']].head(10)

    # Now, search for additional details from Elasticsearch based on RecipeId in top_10_recipes
    top_10_recipes_details = []
    for recipe in top_10_recipes['RecipeId']:
        es_query = {
            "query": {
                "term": {
                    "RecipeId": recipe
                }
            }
        }
        es_response = es.search(index="recipes", body=es_query)

        if es_response['hits']['hits']:
            recipe_details = es_response['hits']['hits'][0]['_source']
            # Add the predicted rating to the recipe details
            recipe_details['PredictedRating'] = top_10_recipes[top_10_recipes['RecipeId'] == recipe]['PredictedRating'].values[0]
            # Clean the image URL
            recipe_details['Images'] = clean_image_url(recipe_details.get('Images'))
            print('each: ',recipe_details['Images'])
            top_10_recipes_details.append(recipe_details)
    print('top 10', top_10_recipes_details)

    # Send the recipe details to the front-end
    return render_template('bookmark_details.html', folder=folder, bookmarks=bookmarks, top_10_recipes=top_10_recipes_details)




@app.route('/all-bookmarks')
@login_required
def show_all_bookmarks_page():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    # Fetch bookmarks that are associated with a valid folder (i.e., exclude 'Uncategorized' folders)
    bookmarks = Bookmark.query.filter_by(user_id=user_id).filter(Bookmark.folder_id != None).all()

    # Prepare formatted data with ratings
    formatted_bookmarks = [
        {
            "recipe_id": bookmark.recipe_id,
            "recipe_name": bookmark.recipe_name,
            "recipe_image": bookmark.recipe_images,
            "recipe_description": bookmark.recipe_description,
            "rating": bookmark.rating,  # Directly use the rating from the Bookmark model
            "folder_name": bookmark.folder.name if bookmark.folder else "Uncategorized"
        }
        for bookmark in bookmarks
    ]

    # Sort the bookmarks by rating in descending order
    formatted_bookmarks.sort(key=lambda x: x['rating'], reverse=True)

    return render_template('all_bookmarks.html', bookmarks=formatted_bookmarks)


@app.route('/delete_folder', methods=['POST'])
@login_required
def delete_folder():
    # Get the folder ID from the query parameters
    folder_id = request.args.get('folder_id', type=int)

    # Fetch the folder from the database
    folder = BookmarkFolder.query.get_or_404(folder_id)

    # Check if the folder belongs to the logged-in user
    if folder.user_id != session.get('user_id'):
        # If the folder doesn't belong to the current user, show an error
        return "You are not authorized to delete this folder.", 403

    # Update bookmarks that are associated with this folder (set folder_id to NULL or delete)
    bookmarks = Bookmark.query.filter_by(folder_id=folder_id).all()

    for bookmark in bookmarks:
        # Set the folder_id of the bookmark to None, or you could delete the bookmarks if needed
        bookmark.folder_id = None  # This removes the folder association
        db.session.add(bookmark)

    # Commit changes to the database (update bookmarks first)
    db.session.commit()

    # Now delete the folder itself
    db.session.delete(folder)

    # Commit again after deleting the folder
    db.session.commit()

    # Redirect back to the folder list page after deletion
    return redirect(url_for('show_all_bookmarks_page'))  # Or wherever you'd like to redirect

@app.route('/suggestion', methods=['GET'])
@login_required
def suggestion():
    # Read the top_predictions CSV file into a pandas DataFrame
    suggestion = pd.read_csv('resource/top_predictions.csv')

    # Create an empty list to hold the final suggestions
    final_suggestions = []

    # Loop through each RecipeId in the DataFrame and search in Elasticsearch
    for recipe_id in suggestion['RecipeId']:
        # Perform a search query in Elasticsearch to get details for each RecipeId
        response = es.search(index="recipes", body={
            "query": {
                "match": {
                    "RecipeId": recipe_id
                }
            }
        })

        # If there is a match in Elasticsearch, append the relevant data to final_suggestions
        if response['hits']['total']['value'] > 0:
            recipe_data = response['hits']['hits'][0]['_source']
            
            # Clean the image URL using clean_image_url
            cleaned_image_url = clean_image_url(recipe_data.get('Images'))
            
            final_suggestions.append({
                "RecipeId": recipe_data.get('RecipeId'),
                "Name": recipe_data.get('Name'),
                "Description": recipe_data.get('Description'),
                "Images": cleaned_image_url  # Use cleaned image URL
            })

    # Get 10 random recipe IDs
    random_recipe_ids = [random.randint(1, 200000) for _ in range(5)]

    # Perform a search for multiple RecipeIds using 'terms' query
    random_response = es.search(index="recipes", body={
        "query": {
            "terms": {
                "RecipeId": random_recipe_ids
            }
        },
        "size": 5  # We want 10 random recipes
    })

    # Create a list to hold random recipes
    random_recipes = []
    for hit in random_response['hits']['hits']:
        random_recipe_data = hit['_source']
        random_recipe_image = clean_image_url(random_recipe_data.get('Images'))
        
        random_recipes.append({
            "RecipeId": random_recipe_data.get('RecipeId'),
            "Name": random_recipe_data.get('Name'),
            "Description": random_recipe_data.get('Description'),
            "Images": random_recipe_image
        })

    # Return both the regular and random suggestions
    return render_template('suggestion.html', suggestion=final_suggestions, random_recipes=random_recipes)




@app.route('/logout', methods=['GET'])
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
    
def load_pickle(file_name):
    """Load a pickled object if it exists, otherwise return None."""
    if os.path.exists(file_name):
        with open(file_name, "rb") as f:
            return pickle.load(f)
    return None

def save_pickle(obj, file_name):
    """Save an object to a pickle file."""
    with open(file_name, "wb") as f:
        pickle.dump(obj, f)

def preprocess_data(df, fit_encoder=False, fit_imputer=False):
    features = ['RecipeId', 'AggregatedRating', 'ReviewCount', 'Calories', 'FatContent', 'SaturatedFatContent',
                'CholesterolContent', 'SodiumContent', 'CarbohydrateContent', 'FiberContent', 'SugarContent',
                'ProteinContent', 'RecipeServings', 'RecipeCategory']
    
    df = df[features].copy()  # Ensure a copy to avoid modifying the original DataFrame
    
    # Load or create LabelEncoder
    encoder = load_pickle("encoder.pkl") if not fit_encoder else LabelEncoder()
    
    # Encode 'RecipeCategory'
    if fit_encoder:
        df['RecipeCategory'] = encoder.fit_transform(df['RecipeCategory'])
        save_pickle(encoder, "encoder.pkl")  # Save the fitted encoder
    else:
        df['RecipeCategory'] = encoder.transform(df['RecipeCategory'])
    
    # Select columns for imputation
    null_col = ['AggregatedRating', 'ReviewCount', 'RecipeServings']
    
    # Load or create SimpleImputer
    imputer = load_pickle("imputer.pkl") if not fit_imputer else SimpleImputer(strategy='mean')
    
    # Fit and transform for training, only transform for test data
    if fit_imputer:
        imputed_values = imputer.fit_transform(df[null_col])
        save_pickle(imputer, "imputer.pkl")  # Save the fitted imputer
    else:
        imputed_values = imputer.transform(df[null_col])
    
    df[null_col] = pd.DataFrame(imputed_values, columns=null_col, index=df.index)
    
    # Drop 'RecipeCategory'
    df = df.drop(columns=['RecipeCategory'])
    
    return df
if __name__ == '__main__':
    app.run(debug=True)
