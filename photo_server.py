from flask import Flask, send_file, render_template_string, request, redirect, url_for
import os
import tempfile
import logging
from telegram import Bot
import sqlite3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Configuration
DATABASE_FILE = "bot_data.db"
TOKEN = os.environ.get("TOKEN")
TEMP_DIR = tempfile.mkdtemp()
bot = None

# Initialize bot if token is available
if TOKEN:
    bot = Bot(token=TOKEN)

def get_db_connection():
    """Establish and return a database connection."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """Main page showing claims list with pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5, type=int)
    driver_id = request.args.get('driver_id', None, type=int)
    
    # Get all drivers for the filter dropdown
    drivers = []
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name FROM drivers")
        for row in cursor.fetchall():
            name = f"@{row['username']}" if row['username'] else row['first_name']
            drivers.append({'id': row['user_id'], 'name': name})
    except Exception as e:
        logger.error(f"Error fetching drivers: {e}")
    finally:
        conn.close()
    
    # Get claims with pagination
    claims = []
    total_claims = 0
    total_pages = 1
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Count query
        count_query = "SELECT COUNT(*) FROM claims"
        params = []
        if driver_id:
            count_query += " WHERE driver_id = ?"
            params.append(driver_id)
        
        cursor.execute(count_query, params)
        total_claims = cursor.fetchone()[0]
        total_pages = (total_claims + per_page - 1) // per_page if total_claims > 0 else 1
        
        # Data query
        offset = (page - 1) * per_page
        data_query = """
            SELECT c.claim_id, c.driver_id, c.date, c.type, c.amount, c.photo_file_id,
                   d.username, d.first_name
            FROM claims c
            LEFT JOIN drivers d ON c.driver_id = d.user_id
        """
        if driver_id:
            data_query += " WHERE c.driver_id = ?"
        
        data_query += " ORDER BY c.date DESC, c.claim_id DESC LIMIT ? OFFSET ?"
        params.append(per_page)
        params.append(offset)
        
        cursor.execute(data_query, params)
        for row in cursor.fetchall():
            driver_name = f"@{row['username']}" if row['username'] else row['first_name'] or f"User {row['driver_id']}"
            claims.append({
                'id': row['claim_id'],
                'driver_id': row['driver_id'],
                'driver_name': driver_name,
                'date': row['date'],
                'type': row['type'],
                'amount': row['amount'],
                'photo_file_id': row['photo_file_id']
            })
    except Exception as e:
        logger.error(f"Error fetching claims: {e}")
    finally:
        conn.close()
    
    # HTML template
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Driver Claims</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                max-width: 1000px;
                margin: 0 auto;
                background-color: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                margin-top: 0;
            }
            .filters {
                margin-bottom: 20px;
                padding: 15px;
                background-color: #f9f9f9;
                border-radius: 5px;
            }
            .claim-card {
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 15px;
                margin-bottom: 15px;
                background-color: white;
            }
            .claim-header {
                display: flex;
                justify-content: space-between;
                margin-bottom: 10px;
            }
            .claim-details {
                margin-bottom: 10px;
            }
            .claim-photo {
                text-align: center;
            }
            .claim-photo img {
                max-width: 100%;
                max-height: 200px;
                border-radius: 5px;
            }
            .pagination {
                margin-top: 20px;
                text-align: center;
            }
            .pagination a, .pagination span {
                display: inline-block;
                padding: 8px 16px;
                text-decoration: none;
                color: black;
                border: 1px solid #ddd;
                margin: 0 4px;
            }
            .pagination a:hover {
                background-color: #ddd;
            }
            .pagination .active {
                background-color: #4CAF50;
                color: white;
                border: 1px solid #4CAF50;
            }
            .modal {
                display: none;
                position: fixed;
                z-index: 1;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                overflow: auto;
                background-color: rgba(0,0,0,0.9);
            }
            .modal-content {
                margin: auto;
                display: block;
                max-width: 90%;
                max-height: 90%;
            }
            .close {
                position: absolute;
                top: 15px;
                right: 35px;
                color: #f1f1f1;
                font-size: 40px;
                font-weight: bold;
                transition: 0.3s;
            }
            .close:hover, .close:focus {
                color: #bbb;
                text-decoration: none;
                cursor: pointer;
            }
            select, button {
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #ddd;
            }
            button {
                background-color: #4CAF50;
                color: white;
                border: none;
                cursor: pointer;
            }
            button:hover {
                background-color: #45a049;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Driver Claims</h1>
            
            <div class="filters">
                <form action="/" method="get">
                    <label for="driver_id">Filter by Driver:</label>
                    <select name="driver_id" id="driver_id">
                        <option value="">All Drivers</option>
                        {% for driver in drivers %}
                        <option value="{{ driver.id }}" {% if driver_id == driver.id %}selected{% endif %}>
                            {{ driver.name }}
                        </option>
                        {% endfor %}
                    </select>
                    <button type="submit">Apply Filter</button>
                    {% if driver_id %}
                    <a href="/" style="margin-left: 10px;">Clear Filter</a>
                    {% endif %}
                </form>
            </div>
            
            {% if claims %}
                {% for claim in claims %}
                <div class="claim-card">
                    <div class="claim-header">
                        <div><strong>{{ claim.driver_name }}</strong></div>
                        <div>{{ claim.date }}</div>
                    </div>
                    <div class="claim-details">
                        <div><strong>Type:</strong> {{ claim.type }}</div>
                        <div><strong>Amount:</strong> RM{{ "%.2f"|format(claim.amount) }}</div>
                    </div>
                    {% if claim.photo_file_id %}
                    <div class="claim-photo">
                        <img src="{{ url_for('photo', file_id=claim.photo_file_id) }}" 
                             alt="Claim photo" 
                             onclick="openModal('{{ url_for('photo', file_id=claim.photo_file_id) }}')">
                        <div>
                            <a href="{{ url_for('download_photo', file_id=claim.photo_file_id) }}" target="_blank">Download Photo</a>
                        </div>
                    </div>
                    {% else %}
                    <div class="claim-photo">No photo available</div>
                    {% endif %}
                </div>
                {% endfor %}
                
                <div class="pagination">
                    {% if page > 1 %}
                    <a href="{{ url_for('index', page=page-1, driver_id=driver_id) }}">&laquo; Previous</a>
                    {% endif %}
                    
                    {% for p in range(1, total_pages + 1) %}
                        {% if p == page %}
                        <span class="active">{{ p }}</span>
                        {% else %}
                        <a href="{{ url_for('index', page=p, driver_id=driver_id) }}">{{ p }}</a>
                        {% endif %}
                    {% endfor %}
                    
                    {% if page < total_pages %}
                    <a href="{{ url_for('index', page=page+1, driver_id=driver_id) }}">Next &raquo;</a>
                    {% endif %}
                </div>
            {% else %}
                <p>No claims found.</p>
            {% endif %}
        </div>
        
        <!-- Modal for image preview -->
        <div id="imageModal" class="modal">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="modalImg">
        </div>
        
        <script>
            function openModal(imgSrc) {
                var modal = document.getElementById("imageModal");
                var modalImg = document.getElementById("modalImg");
                modal.style.display = "block";
                modalImg.src = imgSrc;
            }
            
            function closeModal() {
                document.getElementById("imageModal").style.display = "none";
            }
            
            // Close modal when clicking outside the image
            window.onclick = function(event) {
                var modal = document.getElementById("imageModal");
                if (event.target == modal) {
                    modal.style.display = "none";
                }
            }
        </script>
    </body>
    </html>
    """
    
    return render_template_string(
        html_template, 
        claims=claims, 
        drivers=drivers,
        page=page, 
        total_pages=total_pages,
        driver_id=driver_id
    )

@app.route('/photo/<file_id>')
def photo(file_id):
    """Serve a photo by its Telegram file_id."""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        # Check if we already have this photo cached
        photo_path = os.path.join(TEMP_DIR, f"{file_id}.jpg")
        
        if not os.path.exists(photo_path):
            # Download from Telegram
            file = bot.get_file(file_id)
            file.download(photo_path)
        
        return send_file(photo_path, mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"Error serving photo {file_id}: {e}")
        return "Error loading photo", 500

@app.route('/download/<file_id>')
def download_photo(file_id):
    """Download a photo by its Telegram file_id."""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        # Check if we already have this photo cached
        photo_path = os.path.join(TEMP_DIR, f"{file_id}.jpg")
        
        if not os.path.exists(photo_path):
            # Download from Telegram
            file = bot.get_file(file_id)
            file.download(photo_path)
        
        return send_file(photo_path, mimetype='image/jpeg', 
                         download_name=f"claim_{file_id}.jpg", 
                         as_attachment=True)
    except Exception as e:
        logger.error(f"Error downloading photo {file_id}: {e}")
        return "Error downloading photo", 500

@app.route('/telegram-webhook')
def telegram_webhook():
    """Redirect to the main Telegram bot webhook."""
    return redirect(url_for('index'))

if __name__ == '__main__':
    # For local testing only
    app.run(debug=True, host='0.0.0.0', port=5001)
