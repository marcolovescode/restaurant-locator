# Restaurant Locator

A React web application to search a restaurant critic's reviews on a map. My goal is to enable
proximity searches on reviews that were tagged by the restaurant's general neighborhood.

The front-end and back-end are under development. The only available code is of the scraper.

## Directory Structure

* `/scraper` -- Python scripts to scrape the blog for review content.

## License

Copyright (C) 2019-2020 Marco Zafra, All Rights Reserved.

I intend to license this project permissively once it becomes more substantial.

# The Plan

The app will resemble a map-enhanced places listing, similar to
[HappyCow](https://www.happycow.net/) for Vegan restaurants. The user will be able to:

* Browse a listing of restaurants by cuisine type
* Search for restaurants by name, zip code, and other filters
* View a restaurant page containing the critic's review and links to Google Maps, Yelp, and food
delivery services.
* Bookmark favorite restaurants and issue feedback as to the restaurant's status (e.g., closed
permanently) and services offered (e.g., delivery)

I am planning to develop on the following stack:

### Front-end: Elastic UI, React, TypeScript

React represents a procedural approach towards wiring page markup to JavaScript behavior. I believe
it is reminiscent of how native applications are written. I look forward to using TypeScript
because it encourages better code through its strong typing.

To enable a mobile-first design, I settled on [Elastic UI](https://github.com/elastic/eui) due to
its accessibility (a11y) support, its large component collection, and the maturity of its codebase.

To reduce server costs and help search engine compatibility (SEO), I may use static site
generation (SSG) via next.js and deploy the app on a Jamstack service such as Netlify.
This is feasible because I only need to track 500 restaurants and the content rarely changes.

### Back-end: MongoDB, GraphQL, next.js, node.js

The back-end would be responsible for fulfilling search requests via GraphQL and enabling user
accounts authenticated with OAuth2 to sync bookmarks and issue feedback.

I considered designing without MongoDB, but it would be useful to enable the user account
features. Otherwise, it would be simpler to serve restaurants from a JSON list because I am
only tracking 500 entities.

I may make the back-end optional, not required. To enable full offline capability, I could fulfill
GraphQL from a local store by using
[`react-relay-offline`](https://github.com/morrys/react-relay-offline) and disable the user account
features.
