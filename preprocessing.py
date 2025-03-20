import pandas as pd
import pickle
import os
from sklearn.preprocessing import LabelEncoder
from sklearn.impute import SimpleImputer

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

def preprocess_data(df, fit_encoder=True, fit_imputer=True):
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

test_data_2 = pd.read_csv('for_model.csv')
# Test data (using saved encoder and imputer)
test_data = preprocess_data(test_data_2, fit_encoder=False, fit_imputer=False)
