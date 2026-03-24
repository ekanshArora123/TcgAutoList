import requests 
import mysql.connector
import os
from dotenv import load_dotenv
from datetime import date

algorithm = 2.0 #6/19/25
day = date.today()
load_dotenv()
def main():
    priceTuple = priceSqlCards()
    print(f"Listings: {priceTuple[0]}, Sales: {priceTuple[1]}")


def priceSqlCards():
    #Prices actualInventory
    
    mydb = mysql.connector.connect(
        host="localhost",
        user= os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        database=os.getenv('MYSQL_DATABASE'),
        port= os.getenv('MYSQL_PORT')
    )
    cards = getCards(mydb)
    return priceEveryCard(mydb, cards)

def priceEveryCard(mydb, cards):
    count = 0
    price = (0,0) # (listings price, sales price)
    for row in cards:
        count += 1
        #temp = (sales price(price, confidence), listings price(price, confidence))
        temp = priceSingularCard(row)
        price[0] += temp[0][0] #listings price
        price[1] += temp[1][0] #sales price

        #writeToSql(mydb, temp[0], temp[1])
    return price

def priceSingularCard(row):
    index = row[0]
    conditions = reFormatCondition(row[1])
    specialtyOne = row[3]
    # specialtyTwo = row[4]
    setName = row[5]
    sku = row[6]
    finish = reFormatFinish(row[2], specialtyOne, setName)

    #(price, confidence)
    listingsTuple = findAndCalculateListingsPrice(index, conditions, finish)
    salesTuple = findAndCalculateSalesPrice(index, conditions, finish)
    priceTuple = (listingsTuple, salesTuple) # (listings price, sales price)
    return priceTuple

def findAndCalculateListingsPrice(index, condition, finish):
    rawData = getListingsPriceFromApi(index, condition, finish)
    if(rawData == -1):
        return (0, -1)
    cleanData = cleanRawListingsData(rawData)
    
    if(len(cleanData) == 0):
        return (0, 0)
    return getLowestListingFromCleanedData(cleanData, condition)

def getLowestListingFromCleanedData(data, condition):
    lowestListing = data[0]
    #calculate confidence
    confidence = 0
    #if condition == 5: #Near Mint smtg
    return (lowestListing, confidence)

def cleanRawListingsData(data):
    newData = []
    for row in data:
        if(row["sellerRating"] != "NA" and row["sellerSales"] != "NA"):
            if((int(row["sellerRating"]) > 80) and ((row["sellerSales"][-1] == "+") or (int(row["sellerSales"]) > 30))):
                newData.append(row["score"]) #score is price + shipping
    return newData

def getListingsPriceFromApi(tcgIndex, condition, finish):
    url = "https://mp-search-api.tcgplayer.com/v1/product/%s/listings" % tcgIndex

    querystring = {"mpfev": "2163"}

    payload = {
        "filters": {
            "term": {
                "sellerStatus": "Live",
                "channelId": 0,
                "language": ["English"],
                "condition": condition,
                "printing": finish,
                "listingType": "standard"
            },
            "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0}
        },
        "from": 0,  # Initialize currentListingCount here
        "size": 50,
        "sort": {
            "field": "price+shipping",
            "order": "asc"
        },
        "context": {
            "shippingCountry": "US",
            "cart": {}
        },
        "aggregations": ["listingType"]
    }
    headers = {
        "authority": "mp-search-api.tcgplayer.com",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.tcgplayer.com",
        "referer": "https://www.tcgplayer.com/",
        "sec-ch-ua": "Not A Brand;v=99, Google Chrome;v=121, Chromium;v=121",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }

    response = requests.Session().request("POST", url, json=payload, headers=headers, params=querystring).json()
    if(len(response) > 0):
        response = response["results"][0]
    else:
        return -1
    data = response["results"]
    return data
    

def findAndCalculateSalesPrice(index, conditions, finish):
    return getSalesPriceFromApi(index, conditions, finish)

def getSalesPriceFromApi(index, conditions, finish):
    return 0

def reFormatFinish(finish, edition, setName):
    if(edition == "First Edition"):
        if(finish == "Holo"):
            return "1st Edition Holofoil"
        else:
            return "1st Edition"
    else:
       
        if(setName in ("Base Set (Shadowless)", "Jungle", "Fossil", "Gym Challenge", "Gym Heroes", "Team Rocket", "Neo Genesis", "Neo Discovery", "Neo Revelation", "Neo Destiny")):
            
            if(finish == "Holo"):
                return "Unlimited Holofoil"
            return "Unlimited"
        
        if(finish == "Reverse-Holo"):
            return "Reverse Holofoil"
        if(finish == "Holo"):
            return "Holofoil"
        return "Normal"
    
def reFormatCondition(cond):
    if(cond == "DM"):
        return ("Damaged",)
    if(cond == "DM-HP"):
        return ("Damaged", "Heavily Played")
    if(cond == "HP"):
        return ("Heavily Played",)
    if(cond == "HP-MP"):
        return ("Heavily Played",  "Moderately Played")
    if(cond == "MP"):
        return ( "Moderately Played",)
    if(cond == "MP-LP"):
        return ("Moderately Played", "Lightly Played")
    if(cond == "LP"):
        return ( "Lightly Played",)
    if(cond == "LP-NM"):
        return ("Lightly Played","Near Mint")
    if(cond == "NM" or cond == "MINT"):
        return ( "Near Mint",)
    else:
        return ("Damaged",)
    
def getCards(mydb):
    cursor = mydb.cursor()
    cursor.execute("""
    SELECT s.ID, s.cardCondition, s.cardFinish, s.specialtyOne, s.specialtyTwo, 
           c.setName, s.hashedSku
    FROM skutable s
    JOIN cardinfo c ON s.ID = c.ID
    JOIN actualinventory ai ON ai.pricingSku = s.hashedSku
    WHERE (s.latestCalcDate IS NULL OR s.latestCalcDate < CURDATE() - INTERVAL 14 DAY)
    """)

    return cursor.fetchall()
    
main()