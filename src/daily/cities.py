"""
Cities the daily puzzle draws from - the single source of truth, imported by
both the builder (app.py) and the local sample generator. No dependencies, so
it imports anywhere.

Each name must match Commons' category naming, i.e. the "<name>" in
"Category:<year> in <name>". Coordinates are geocoded and cached, so adding a
city is just adding a name. The list is broad (world capitals plus major
cities); names without a Commons category are simply never picked, so the odd
miss is harmless.
"""

CITIES = [
    # ---- Europe --------------------------------------------------------------
    "London", "Birmingham", "Manchester", "Liverpool", "Leeds", "Glasgow",
    "Edinburgh", "Cardiff", "Belfast", "Dublin", "Cork",
    "Paris", "Marseille", "Lyon", "Toulouse", "Nice", "Bordeaux", "Strasbourg",
    "Berlin", "Hamburg", "Munich", "Cologne", "Frankfurt", "Stuttgart",
    "Düsseldorf", "Dresden", "Leipzig", "Bremen", "Hanover", "Nuremberg",
    "Rome", "Milan", "Naples", "Turin", "Florence", "Venice", "Bologna",
    "Genoa", "Palermo",
    "Madrid", "Barcelona", "Valencia", "Seville", "Zaragoza", "Málaga",
    "Bilbao", "Granada",
    "Lisbon", "Porto", "Amsterdam", "Rotterdam", "The Hague", "Utrecht",
    "Brussels", "Antwerp", "Ghent", "Bruges", "Luxembourg",
    "Vienna", "Graz", "Salzburg", "Zurich", "Geneva", "Basel", "Bern",
    "Prague", "Brno", "Bratislava", "Budapest", "Warsaw", "Kraków", "Wrocław",
    "Łódź", "Gdańsk", "Poznań", "Bucharest", "Cluj-Napoca", "Sofia", "Belgrade",
    "Zagreb", "Ljubljana", "Sarajevo", "Skopje", "Podgorica", "Pristina",
    "Tirana", "Athens", "Thessaloniki", "Nicosia", "Valletta",
    "Moscow", "Saint Petersburg", "Kazan", "Novosibirsk", "Yekaterinburg",
    "Kyiv", "Kharkiv", "Lviv", "Odesa", "Minsk", "Chișinău",
    "Stockholm", "Gothenburg", "Malmö", "Oslo", "Bergen", "Copenhagen",
    "Helsinki", "Reykjavík", "Riga", "Tallinn", "Vilnius",

    # ---- Middle East & Caucasus & Central Asia ------------------------------
    "Istanbul", "Ankara", "Izmir", "Jerusalem", "Tel Aviv", "Beirut",
    "Damascus", "Amman", "Baghdad", "Riyadh", "Jeddah", "Mecca", "Doha",
    "Dubai", "Abu Dhabi", "Manama", "Kuwait City", "Muscat", "Sanaa",
    "Tehran", "Isfahan", "Baku", "Tbilisi", "Yerevan", "Astana", "Almaty",
    "Tashkent", "Samarkand", "Bishkek", "Dushanbe", "Ashgabat", "Kabul",

    # ---- South & East Asia ---------------------------------------------------
    "New Delhi", "Delhi", "Mumbai", "Kolkata", "Chennai", "Bangalore",
    "Hyderabad", "Ahmedabad", "Jaipur", "Islamabad", "Karachi", "Lahore",
    "Dhaka", "Kathmandu", "Colombo", "Thimphu", "Malé",
    "Beijing", "Shanghai", "Guangzhou", "Shenzhen", "Chengdu", "Wuhan",
    "Xi'an", "Nanjing", "Hangzhou", "Tianjin", "Hong Kong", "Macau",
    "Taipei", "Tokyo", "Osaka", "Kyoto", "Yokohama", "Nagoya", "Sapporo",
    "Fukuoka", "Hiroshima", "Seoul", "Busan", "Pyongyang", "Ulaanbaatar",
    "Bangkok", "Chiang Mai", "Hanoi", "Ho Chi Minh City", "Phnom Penh",
    "Vientiane", "Naypyidaw", "Yangon", "Singapore", "Kuala Lumpur",
    "Jakarta", "Surabaya", "Bandung", "Manila", "Cebu City", "Bandar Seri Begawan",

    # ---- Africa --------------------------------------------------------------
    "Cairo", "Alexandria", "Giza", "Casablanca", "Rabat", "Marrakesh",
    "Algiers", "Tunis", "Tripoli", "Khartoum", "Addis Ababa", "Nairobi",
    "Mombasa", "Kampala", "Dar es Salaam", "Dodoma", "Kigali", "Lagos",
    "Abuja", "Kano", "Ibadan", "Accra", "Abidjan", "Dakar", "Bamako",
    "Ouagadougou", "Niamey", "Conakry", "Freetown", "Monrovia", "Lomé",
    "Cotonou", "Yaoundé", "Douala", "Libreville", "Brazzaville", "Kinshasa",
    "Luanda", "Lusaka", "Harare", "Lilongwe", "Maputo", "Gaborone",
    "Windhoek", "Johannesburg", "Cape Town", "Durban", "Pretoria",
    "Port Elizabeth", "Antananarivo", "Port Louis",

    # ---- Oceania -------------------------------------------------------------
    "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Canberra",
    "Auckland", "Wellington", "Christchurch", "Suva", "Port Moresby",

    # ---- North America -------------------------------------------------------
    "New York City", "Los Angeles", "Chicago", "Houston", "Phoenix",
    "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Francisco",
    "Seattle", "Denver", "Boston", "Washington, D.C.", "Detroit", "Atlanta",
    "Miami", "Minneapolis", "New Orleans", "Pittsburgh", "Baltimore",
    "St. Louis", "Las Vegas", "Portland, Oregon", "Honolulu",
    "Toronto", "Montreal", "Vancouver", "Calgary", "Ottawa", "Edmonton",
    "Quebec City", "Winnipeg", "Halifax, Nova Scotia",
    "Mexico City", "Guadalajara", "Monterrey", "Puebla", "Tijuana",

    # ---- Central America & Caribbean ----------------------------------------
    "Guatemala City", "San Salvador", "Tegucigalpa", "Managua", "San José",
    "Panama City", "Havana", "Santo Domingo", "Kingston", "Port-au-Prince",
    "San Juan", "Nassau", "Bridgetown",

    # ---- South America -------------------------------------------------------
    "Bogotá", "Medellín", "Cali", "Caracas", "Quito", "Guayaquil", "Lima",
    "La Paz", "Santa Cruz de la Sierra", "Asunción", "Montevideo",
    "Buenos Aires", "Córdoba", "Rosario", "Santiago", "Valparaíso",
    "Rio de Janeiro", "São Paulo", "Brasília", "Salvador", "Belo Horizonte",
    "Recife", "Fortaleza", "Porto Alegre", "Curitiba", "Manaus", "Georgetown",
    "Paramaribo",
]
