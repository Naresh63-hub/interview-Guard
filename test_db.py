import os
from dotenv import load_dotenv
load_dotenv()
from database import MongoDBDatabase
db = MongoDBDatabase()
db.connect()
if db.is_connected():
    print("MongoDB Connected!")
else:
    print("MongoDB Connection Failed!")
