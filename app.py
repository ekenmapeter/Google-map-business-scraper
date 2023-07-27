from flask import Flask, render_template, request, redirect, url_for, session, make_response, send_file
from flask_mysqldb import MySQL
import MySQLdb.cursors
import re
from playwright.sync_api import sync_playwright
from dataclasses import dataclass, asdict, field
import pandas as pd
import argparse
from datetime import datetime 
import asyncio
import os


app = Flask(__name__)

app.secret_key = 'xyzkjdhfhfgfgghrhrhrsdfg'

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'flask_google_map'

mysql = MySQL(app)


@dataclass
class Business:
    """holds business data"""

    name: str = None
    address: str = None
    website: str = None
    phone_number: str = None
    reviews_count: int = None
    reviews_average: float = None


@dataclass
class BusinessList:
    """holds list of Business objects,
    and save to both excel and csv
    """

    business_list: list[Business] = field(default_factory=list)

    def dataframe(self):
        """transform business_list to pandas dataframe

        Returns: pandas dataframe
        """
        return pd.json_normalize(
            (asdict(business) for business in self.business_list), sep="_"
        )

    def save_to_excel(self, filename):
        """saves pandas dataframe to excel (xlsx) file

        Args:
            filename (str): filename
        """
        self.dataframe().to_excel(f"{filename}.xlsx", index=False)

    def save_to_csv(self, filename):
        """saves pandas dataframe to csv file

        Args:
            filename (str): filename
        """
        self.dataframe().to_csv(f"{filename}.csv", index=False)



@app.route('/')
@app.route('/login', methods=['GET', 'POST'])
def login():
    message = ''
    if request.method == 'POST' and 'email' in request.form and 'password' in request.form:
        email = request.form['email']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM user WHERE email = %s AND password = %s', (email, password,))
        user = cursor.fetchone()
        if user:
            session['loggedin'] = True
            session['userid'] = user['userid']
            session['name'] = user['name']
            session['email'] = user['email']
            message = 'Logged in successfully!'
            return render_template('user.html', message=message)
        else:
            message = 'Please enter correct email/password!'
    return render_template('login.html', message=message)



@app.route('/logout')
def logout():
    session.pop('loggedin', None)
    session.pop('userid', None)
    session.pop('email', None)
    return redirect(url_for('login'))


@app.route('/download_csv/<int:id>')
def download_csv(id):
    # Check if the id exists in the scraped_data table
    cursor = mysql.connection.cursor()
    cursor.execute('SELECT title, csv_path FROM scraped_data WHERE id = %s', (id,))
    result = cursor.fetchone()
    cursor.close()

    if result is None:
        return "CSV file not found for the given id", 404

    title, csv_path = result

    p = csv_path

    return send_file(p,as_attachment=True)





@app.route('/register', methods=['GET', 'POST'])
def register():
    message = ''
    if request.method == 'POST' and 'name' in request.form and 'password' in request.form and 'email' in request.form:
        userName = request.form['name']
        password = request.form['password']
        email = request.form['email']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM user WHERE email = %s', (email,))
        account = cursor.fetchone()
        if account:
            message = 'Account already exists!'
        elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
            message = 'Invalid email address!'
        elif not userName or not password or not email:
            message = 'Please fill out the form!'
        else:
            cursor.execute('INSERT INTO user VALUES (NULL, %s, %s, %s)', (userName, email, password,))
            mysql.connection.commit()
            message = 'You have successfully registered!'
    elif request.method == 'POST':
        message = 'Please fill out the form!'
    return render_template('register.html', message=message)


        
def generate_unique_filename(search_for):
    # Remove special characters and spaces from the search query
    cleaned_search_for = re.sub(r'[^\w]', '', search_for)

    # Hash the cleaned search query
    hash_key = str(hash(search_for)).replace('-', '')  # Remove hyphens from the hash key

    # Combine the cleaned search query and hash key without any separator
    unique_filename = f"{cleaned_search_for}{hash_key}"
    return unique_filename

def main(search_for, city_country, total):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://www.google.com/maps", timeout=40000)
        # wait is added for dev phase. can remove it in production
        # page.wait_for_timeout(5000)

        # Combine search_for and city_country with a space in between
        search_query = f"{search_for} {city_country}"
        
        # Fill the combined search_query into the input field
        search_input = page.locator('//input[@id="searchboxinput"]')
        search_input.fill(search_query)
        page.wait_for_timeout(2000)

        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)

        # scrolling
        page.hover('//a[contains(@href, "https://www.google.com/maps/place")]')

        # this variable is used to detect if the bot
        # scraped the same number of listings in the previous iteration
        previously_counted = 0
        while True:
            page.mouse.wheel(0, 10000)
            page.wait_for_timeout(2000)

            if (
                page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count()
                >= total
            ):
                listings = page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()[:total]
                listings = [listing.locator("xpath=..") for listing in listings]
                print(f"Total Scraped: {len(listings)}")
                break
            else:
                # logic to break from loop to not run infinitely
                # in case arrived at all available listings
                if (
                    page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count()
                    == previously_counted
                ):
                    listings = page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()
                    print(f"Arrived at all available\nTotal Scraped: {len(listings)}")
                    break
                else:
                    previously_counted = page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count()
                    print(
                        f"Currently Scraped: ",
                        page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count(),
                    )

        business_list = BusinessList()

        # scraping
        for listing in listings:
            listing.click()
            page.wait_for_timeout(3000)

            name_xpath = '//div[contains(@class, "fontHeadlineSmall")]'
            address_xpath = '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
            website_xpath = '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
            phone_number_xpath = '//button[contains(@data-item-id, "phone:tel:")]//div[contains(@class, "fontBodyMedium")]'
            reviews_span_xpath = '//span[@role="img"]'

            business = Business()

            if listing.locator(name_xpath).count() > 0:
                business.name = listing.locator(name_xpath).inner_text()
            else:
                business.name = ""
            if page.locator(address_xpath).count() > 0:
                business.address = page.locator(address_xpath).inner_text()
            else:
                business.address = ""
            if page.locator(website_xpath).count() > 0:
                business.website = page.locator(website_xpath).inner_text()
            else:
                business.website = ""
            if page.locator(phone_number_xpath).count() > 0:
                business.phone_number = page.locator(phone_number_xpath).inner_text()
            else:
                business.phone_number = ""
            if listing.locator(reviews_span_xpath).count() > 0:
                business.reviews_average = float(
                    listing.locator(reviews_span_xpath)
                    .get_attribute("aria-label")
                    .split()[0]
                    .replace(",", ".")
                    .strip()
                )
                business.reviews_count = int(
                    listing.locator(reviews_span_xpath)
                    .get_attribute("aria-label")
                    .split()[2]
                    .strip()
                )
            else:
                business.reviews_average = ""
                business.reviews_count = ""

            business_list.business_list.append(business)

        # Generate unique filename based on the search query
        filename_base = generate_unique_filename(search_for)

        # Ensure that the "download" directory exists
        if not os.path.exists("download"):
            os.makedirs("download")

       # Save to both Excel and CSV files with unique filenames
        excel_filepath = f"download/{filename_base}"
        csv_filepath = f"download/{filename_base}"
        business_list.save_to_excel(excel_filepath)
        business_list.save_to_csv(csv_filepath)

        # Update the csv_filepath to include the '.csv' extension
        csv_filepath += '.csv'

        # Save information to the 'scraped_data' table in the database
        cursor = mysql.connection.cursor()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        source = 'google map'
        status = 'completed'

        # Insert the data into the 'scraped_data' table
        cursor.execute(
            'INSERT INTO scraped_data (title, date, source, status, csv_path) VALUES (%s, %s, %s, %s, %s)',
            (filename_base, timestamp, source, status, csv_filepath)
        )

        # Commit the changes to the database
        mysql.connection.commit()
        cursor.close()
        browser.close()



@app.route('/user', methods=['GET', 'POST'])
def user_dashboard():
    if 'loggedin' in session and session['loggedin']:
        if request.method == 'POST':
            # Get the search_for and total values from the submitted form data
            search_for = request.form['search_for']
            city_country = request.form['city_country']
            total = int(request.form['total'])

            # Perform the Google Maps scraping with the provided search_for and total
            main(search_for, city_country, total)

        # Fetch data from the database
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM scraped_data')  # Change "scraped_data" to your actual table name
        data = cursor.fetchall()

        # Pass the data to the template
        return render_template('user.html', session=session, data=data)
    else:
        return redirect(url_for('login'))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
