import time
import pickle
import os
import re  # Make sure to import 're' for regex handling
from elasticsearch.helpers import bulk
import pandas as pd
from elasticsearch import Elasticsearch
from concurrent.futures import ThreadPoolExecutor

class Indexer:
    def __init__(self):
        self.start_time = time.time()
        self.csv_file_path = "resource/recipes.csv"
        self.pickle_file_path = "resource/recipes_index.pkl"
        self.es_client = Elasticsearch("https://localhost:9200", 
                                       basic_auth=("elastic", "Z_3O+lFyJPcXxPB+UvD-"), 
                                       ca_certs="~/http_ca.crt")
        self.init_time = time.time() - self.start_time
        print(f"Initialization took {self.init_time:.4f} seconds")

    def run_indexer(self):
        # Check if the pickle file exists
        if os.path.exists(self.pickle_file_path):
            print(f"Pickle file found at {self.pickle_file_path}. Loading indexed documents...")
            with open(self.pickle_file_path, 'rb') as f:
                indexed_documents = pickle.load(f)
            print("Pickle file loaded successfully!")
            return  # Exit the method without re-indexing if the pickle file is found

        # If pickle file doesn't exist, proceed with indexing
        start_time = time.time()

        # Disable refresh for bulk indexing
        self.es_client.indices.put_settings(index="recipes", body={
            "settings": {
                "index": {
                    "refresh_interval": "-1"  # Disable refresh
                }
            }
        })

        # Delete the index if exists and create a new one
        self.es_client.options(ignore_status=[400, 404]).indices.delete(index='recipes')
        self.es_client.options(ignore_status=400).indices.create(index='recipes')

        # Load data from CSV
        data = pd.read_csv(self.csv_file_path)

        actions = []  # List to hold bulk actions

        indexed_documents = []  # List to store documents for pickling

        def process_row(row):
            row = self.handle_nan_values(row)  # Handle NaN values before creating the document
            
            document = {
                "_op_type": "index",  # Optional: Index operation for bulk
                "_index": "recipes",  # Index name
                "_id": row["RecipeId"],  # Optional: If you want to set a custom ID
                "_source": {
                    "RecipeId": row["RecipeId"],
                    "Name": row["Name"],
                    "AuthorId": row["AuthorId"],
                    "AuthorName": row["AuthorName"],
                    "CookTime": row["CookTime"],
                    "PrepTime": row["PrepTime"],
                    "TotalTime": row["TotalTime"],
                    "DatePublished": row["DatePublished"],
                    "Description": row["Description"],
                    "Images": row["Images"],
                    "RecipeCategory": row["RecipeCategory"],
                    "Keywords": row["Keywords"],
                    "RecipeIngredientQuantities": row["RecipeIngredientQuantities"],
                    "RecipeIngredientParts": row["RecipeIngredientParts"],
                    "AggregatedRating": row["AggregatedRating"],
                    "ReviewCount": row["ReviewCount"],
                    "Calories": row["Calories"],
                    "FatContent": row["FatContent"],
                    "SaturatedFatContent": row["SaturatedFatContent"],
                    "CholesterolContent": row["CholesterolContent"],
                    "SodiumContent": row["SodiumContent"],
                    "CarbohydrateContent": row["CarbohydrateContent"],
                    "FiberContent": row["FiberContent"],
                    "SugarContent": row["SugarContent"],
                    "ProteinContent": row["ProteinContent"],
                    "RecipeServings": row["RecipeServings"],
                    "RecipeYield": row["RecipeYield"],
                    "RecipeInstructions": row["RecipeInstructions"]
                }
            }
            return document

        # Use ThreadPoolExecutor to parallelize the row processing
        with ThreadPoolExecutor() as executor:
            documents = list(executor.map(process_row, [row for idx, row in data.iterrows()]))

        # Perform bulk indexing in batches of 5000 or a suitable number
        for i in range(0, len(documents), 5000):
            batch = documents[i:i + 5000]
            success, failed = bulk(self.es_client, batch)
            print(f"Bulk indexed {len(batch)} documents: {success} successful, {failed} failed")

        # Enable refresh again after bulk indexing
        self.es_client.indices.put_settings(index="recipes", body={
            "settings": {
                "index": {
                    "refresh_interval": "1s"  # Re-enable refresh interval
                }
            }
        })

        # Pickle the indexed documents to a file
        with open(self.pickle_file_path, 'wb') as f:
            pickle.dump(documents, f)
        print("Indexed documents pickled successfully!")

        end_time = time.time() - start_time
        print(f"run_indexer method took {end_time:.4f} seconds")

    def handle_nan_values(self, row):
        # Replace NaN values with 0 for numeric columns
        for column in row.index:
            if isinstance(row[column], (int, float)) and pd.isna(row[column]):
                row[column] = 0  # Or any default value you prefer (e.g., "")
            
            # Special case for the RecipeYield column
            if column == 'RecipeYield':
                # Extract the numeric part from the 'RecipeYield' string (e.g., '4 kebabs' -> 4)
                match = re.match(r"(\d+)", str(row[column]))
                if match:
                    row[column] = int(match.group(1))  # Extracted numeric part
                else:
                    row[column] = 0  # If no numeric value, set it to a default (e.g., 0)
                
        return row


# Run the indexing process
indexer = Indexer()
indexer.run_indexer()
