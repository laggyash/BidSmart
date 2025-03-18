from flask import Flask, flash, render_template, request, redirect, url_for, session
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = 'passkey'

app.config['MYSQL_HOST'] = 'mysql-bidsmart.alwaysdata.net'
app.config['MYSQL_USER'] = 'bidsmart'
app.config['MYSQL_PASSWORD'] = 'jFgLoq6V'
app.config['MYSQL_DB'] = 'bidsmart_db'
app.config['MYSQL_PORT'] = 3306
app.config['MYSQL_CONNECT_TIMEOUT'] = 20 

mysql = MySQL(app)

def init_db():
    cur = mysql.connection.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            role ENUM('user', 'admin') DEFAULT 'user'
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS auction_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            item_name VARCHAR(100) NOT NULL,
            base_price DECIMAL(10,2) NOT NULL,
            image_url TEXT NOT NULL,
            status ENUM('active', 'closed', 'expired', 'deleted') DEFAULT 'active',
            seller_username VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_username) REFERENCES users(username)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS bids (
            id INT AUTO_INCREMENT PRIMARY KEY,
            item_id INT NOT NULL,
            bidder_username VARCHAR(50) NOT NULL,
            bid_amount DECIMAL(10,2) NOT NULL,
            bid_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES auction_items(id),
            FOREIGN KEY (bidder_username) REFERENCES users(username)
        )
    ''')
    mysql.connection.commit()
    cur.close()


@app.before_request
def before_request():
    init_db()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        
        role = request.form['role'] 
        if role == 'admin' and request.form['admin_code'] != 'admin_bidsmart':
            return "Invalid admin registration code!"

        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO users (name, username, email, password, role) VALUES (%s, %s, %s, %s, %s)", 
                        (name, username, email, password, role))
            mysql.connection.commit()
            return redirect(url_for('login'))
        except Exception as e:
            mysql.connection.rollback()
            print("Error:", e)
        finally:
            cur.close()
        
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        
        if user is None:
            flash("Error: Username does not exist.", "error")
            return redirect(url_for('login'))
        
        if not check_password_hash(user[4], password):
            flash("Error: Incorrect password.", "error")
            return redirect(url_for('login'))
        
        session['username'] = user[2]
        session['role'] = user[5]

        return redirect(url_for('user_home' if user[5] == 'user' else 'admin_home'))

    return render_template('login.html')


@app.route('/user_home')
def user_home():
    if 'username' in session:
        username = session['username']
        cur = mysql.connection.cursor()

        cur.execute("SELECT name FROM users WHERE username = %s", (username,))
        user_name = cur.fetchone()[0]
        
        cur.execute("SELECT * FROM auction_items WHERE seller_username = %s AND status = 'active'", (username,))
        active_items = cur.fetchall()

        cur.execute("SELECT * FROM auction_items WHERE seller_username = %s AND status = 'expired'", (username,))
        expired_items = cur.fetchall()

        cur.execute("SELECT * FROM auction_items WHERE seller_username = %s AND status = 'closed'", (username,))
        closed_items = cur.fetchall()

        cur.execute("SELECT * FROM auction_items WHERE seller_username != %s AND status = 'active'", (username,))
        bid_items = cur.fetchall()

        cur.close()
        return render_template('user_home.html',user_name = user_name,username = username, active_items=active_items, expired_items=expired_items, closed_items=closed_items, bid_items=bid_items)
    return redirect(url_for('login'))


@app.route('/submit_item', methods=['POST'])
def submit_item():
    if 'username' in session:
        item_name = request.form['item_name']
        base_price = request.form['base_price']
        image_url = request.form['image_url']
        seller_username = session['username']

        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO auction_items (item_name, base_price, image_url, seller_username, created_at, status) VALUES (%s, %s, %s, %s, NOW(), 'active')",
                    (item_name, base_price, image_url, seller_username))
        mysql.connection.commit()
        cur.close()

        flash("Item added successfully! It will be active for 3 days.", "success")
        return redirect(url_for('user_home'))
    
    flash("You must be logged in to submit an item.", "error")
    return redirect(url_for('login'))

@app.route('/bid', methods=['POST'])
def place_bid():
    if 'username' in session:
        item_id = request.form['item_id']
        bid_amount = float(request.form['bid_amount'])  
        bidder_username = session['username']

        cur = mysql.connection.cursor()

        # Fetch base price and highest bid
        cur.execute("""
            SELECT base_price, COALESCE(MAX(bid_amount), 0) 
            FROM auction_items 
            LEFT JOIN bids ON auction_items.id = bids.item_id 
            WHERE auction_items.id = %s
        """, (item_id,))
        base_price, highest_bid = cur.fetchone()

        # Validate bid amount
        if bid_amount <= base_price or bid_amount <= highest_bid:
            flash("Error: Bid must be higher than the base price and the current highest bid.", "error")
        else:
            cur.execute("INSERT INTO bids (item_id, bidder_username, bid_amount) VALUES (%s, %s, %s)",
                        (item_id, bidder_username, bid_amount))
            mysql.connection.commit()
            flash("Bid placed successfully!", "success")

        cur.close()

    return redirect(request.referrer) 

@app.route('/close_auction', methods=['POST'])
def close_auction():
    if 'username' in session:
        item_id = request.form['item_id']
        cur = mysql.connection.cursor()
        cur.execute("UPDATE auction_items SET status = 'closed' WHERE id = %s AND seller_username = %s", (item_id, session['username']))
        mysql.connection.commit()
        cur.close()
    return redirect(url_for('my_items'))


@app.route('/admin_home')
def admin_home():
    if 'username' in session and session['role'] == 'admin':
        cur = mysql.connection.cursor()
        cur.execute("SELECT id, name, username, email, role FROM users where username != 'admin'")
        users = cur.fetchall()
        cur.execute("SELECT * FROM auction_items WHERE status = 'active'")
        active_items = cur.fetchall()
        cur.execute("SELECT * FROM auction_items WHERE status = 'expired'")
        expired_items = cur.fetchall()
        cur.execute("SELECT * FROM auction_items WHERE status = 'closed'")
        deleted_items = cur.fetchall()
        cur.close()
        
        return render_template('admin_home.html', users=users, active_items=active_items, expired_items=expired_items, deleted_items=deleted_items)
    return redirect(url_for('login'))


@app.route('/change_role/<int:user_id>', methods=['POST'])
def change_role(user_id):
    if 'username' in session and session['role'] == 'admin':
        new_role = request.form['new_role']
        cur = mysql.connection.cursor()
        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))
        mysql.connection.commit()
        cur.close()
    return redirect(url_for('admin_home'))


@app.route('/delete_user/<int:user_id>')
def delete_user(user_id):
    if 'username' in session and session['role'] == 'admin':
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        mysql.connection.commit()
        cur.close()
    return redirect(url_for('admin_home'))


@app.route('/change_status/<int:item_id>', methods=['POST'])
def change_status(item_id):
    if 'username' in session and session['role'] == 'admin':
        new_status = request.form['new_status']
        cur = mysql.connection.cursor()
        cur.execute("UPDATE auction_items SET status = %s WHERE id = %s", (new_status, item_id))
        mysql.connection.commit()
        cur.close()
    return redirect(url_for('admin_home'))


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route('/my_items')
def my_items():
    if 'username' not in session:
        flash("You must be logged in to view your items.", "error")
        return redirect(url_for('login'))

    seller_username = session['username']
    cur = mysql.connection.cursor()

    cur.execute("""
    SELECT ai.id, ai.item_name, ai.base_price, ai.image_url, 
           COALESCE(MAX(b.bid_amount), 'None') AS highest_bid
    FROM auction_items ai
    LEFT JOIN bids b ON ai.id = b.item_id
    WHERE ai.seller_username = %s AND ai.status = 'active'
    GROUP BY ai.id, ai.item_name, ai.base_price, ai.image_url
""", (session['username'],))
    active_items = cur.fetchall()


    cur.execute('''
    SELECT a.id, a.item_name, a.base_price, a.status, 
           COALESCE(u.email, 'Deleted') AS highest_bidder_email,
           a.image_url,  -- Fetch image URL (always present)
           (SELECT MAX(bid_amount) FROM bids WHERE item_id = a.id) AS highest_bid
    FROM auction_items a
    LEFT JOIN (
        SELECT item_id, bidder_username 
        FROM bids 
        WHERE (item_id, bid_amount) IN (
            SELECT item_id, MAX(bid_amount) 
            FROM bids 
            GROUP BY item_id
        )
    ) b ON a.id = b.item_id
    LEFT JOIN users u ON b.bidder_username = u.username
    WHERE a.seller_username = %s AND a.status = 'closed'
''', (seller_username,))

    closed_items = cur.fetchall()

    cur.close()

    return render_template('my_items.html', active_items=active_items, closed_items=closed_items)




@app.route('/bid_items')
def bid_items():
    username = session.get('username')
    cur = mysql.connection.cursor()
    cur.execute("""
    SELECT ai.id, ai.item_name, ai.base_price, ai.image_url, 
           COALESCE(MAX(b.bid_amount), 'None') AS highest_bid
    FROM auction_items ai
    LEFT JOIN bids b ON ai.id = b.item_id
    WHERE ai.status = 'active' AND ai.seller_username != %s
    GROUP BY ai.id, ai.item_name, ai.base_price, ai.image_url
""", (username,))
    bid_items = cur.fetchall()
    cur.close()
    return render_template('bid_items.html', bid_items=bid_items)

def update_auction_status():
    cur = mysql.connection.cursor()

    three_days_ago = datetime.now() - timedelta(days=3)
    cur.execute("SELECT id FROM auction_items WHERE status = 'active' AND created_at <= %s", (three_days_ago,))
    expired_items = cur.fetchall()

    for item in expired_items:
        item_id = item[0]
        cur.execute("""
            SELECT u.email FROM bids b
            JOIN users u ON b.bidder_username = u.username
            WHERE b.item_id = %s
            ORDER BY b.bid_amount DESC LIMIT 1
        """, (item_id,))
        highest_bidder = cur.fetchone()

        if highest_bidder:
            cur.execute("UPDATE auction_items SET status = 'closed' WHERE id = %s", (item_id,))
        else:
            cur.execute("UPDATE auction_items SET status = 'expired' WHERE id = %s", (item_id,))

    mysql.connection.commit()
    cur.close()


scheduler = BackgroundScheduler()
scheduler.add_job(func=update_auction_status, trigger="interval", hours=1) 
scheduler.start()

if __name__ == '__main__':
    app.run(debug=True)
