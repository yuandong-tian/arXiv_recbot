"""
Core functionality for arXiv paper fetching, rating, and database operations.
This module is platform-agnostic and can be used by both Telegram and Slack bots.
"""
import os
import sqlite3
import torch
import joblib
from datetime import datetime, timedelta, UTC
from typing import List, Tuple, Optional, Callable, Any
from arxiv_util import get_arxiv_results, get_arxiv_message
from preference_model import PreferenceModel
from common import global_model_name, global_vectorizer_name, global_dataset_name

# Constants
MAX_RESULTS = 100

# Global model and vectorizer
loaded_model = None
vectorizer = None

# Database connection
conn = None
cursor = None


def initialize_model():
    """Initialize the preference model and vectorizer if they exist."""
    global loaded_model, vectorizer
    
    if os.path.exists(global_model_name):
        vectorizer = joblib.load(global_vectorizer_name)
        loaded_model = PreferenceModel(vectorizer.get_feature_names_out().shape[0], 6)
        loaded_model.load_state_dict(torch.load(global_model_name))
        loaded_model.eval()
        print(f"Loaded {global_model_name} and {global_vectorizer_name}")
    else:
        loaded_model = None
        vectorizer = None


def initialize_database():
    """Initialize the database connection."""
    global conn, cursor
    conn = sqlite3.connect(global_dataset_name)
    cursor = conn.cursor()


def get_rated_papers(keywords: str, backdays: int) -> List[Tuple[float, str, str]]:
    """
    Fetch papers from arXiv and rate them using the preference model.
    
    Args:
        keywords: Comma-separated keywords for arXiv search
        backdays: Number of days to look back for papers
        
    Returns:
        List of tuples (overall_rating, message, entry_id) sorted by rating
    """
    results = get_arxiv_results(keywords.replace(",", " OR "), MAX_RESULTS)
    
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=backdays)
    
    papers_to_send = []
    
    for result in results:
        submitted_date = result.updated
        # Make the submitted date timezone-aware by assuming UTC
        submitted_date = submitted_date.replace(tzinfo=UTC)
        if submitted_date >= yesterday:
            message = get_arxiv_message(result)
            
            if loaded_model and vectorizer:
                # Predict the class of the paper
                X = vectorizer.transform([message])
                X_tensor = torch.tensor(X.toarray(), dtype=torch.float32)
                prediction = loaded_model(X_tensor)
                # Prepend predicted probabilities of all classes to output text
                y_pred_proba = prediction.softmax(dim=1).detach().cpu()
                y_pred_proba = y_pred_proba[0]
                
                # Compute an overall rating for the paper.
                # The rating is a weighted sum of the predicted probabilities of all classes.
                overall_rating = torch.dot(y_pred_proba, torch.arange(y_pred_proba.shape[0]).float()).item()
                
                message = f"// {overall_rating} {y_pred_proba}\n{message}"
            else:
                # No model to load yet
                overall_rating = 0
                message = f"// no model yet\n{message}"
            
            papers_to_send.append((overall_rating, message, result.entry_id))
    
    # Sort papers_to_send by overall_rating in descending order
    papers_to_send.sort(key=lambda x: x[0], reverse=True)
    # Select the top 10 papers
    papers_to_send = papers_to_send[:10]
    
    return papers_to_send


def retrieve_papers_by_tag(tag: str) -> List[str]:
    """
    Retrieve papers from the database that contain the given tag.
    
    Args:
        tag: Tag to search for in comments
        
    Returns:
        List of paper text strings
    """
    if cursor is None:
        initialize_database()
    
    # Retrieve the paper from the database that contains the tags
    cursor.execute('SELECT paper_message_id FROM comments WHERE comment LIKE ?', ('%' + tag + '%',))
    paper_message_ids = cursor.fetchall()
    
    # Get all papers that contain the tags and return
    papers = []
    
    for paper_message_id_tuple in paper_message_ids:
        paper_message_id = paper_message_id_tuple[0]
        # Retrieve the paper from the database
        cursor.execute('SELECT text FROM infos WHERE paper_message_id = ?', (paper_message_id,))
        for paper in cursor.fetchall():
            # Convert the paper to a string
            papers.append(str(paper[0]))
    
    return papers


def save_feedback(feedback_type: str, entry_id: str, user_id: str):
    """
    Save user feedback to the database.
    
    Args:
        feedback_type: Type of feedback (e.g., "rating1", "rating2", etc.)
        entry_id: arXiv entry ID
        user_id: User ID who provided the feedback
    """
    if cursor is None:
        initialize_database()
    
    # Log the feedback (you can extend this to save to database)
    import logging
    logging.info(f"Received feedback: {feedback_type} for paper {entry_id} from user {user_id}")


# Initialize on import
initialize_model()
initialize_database()

