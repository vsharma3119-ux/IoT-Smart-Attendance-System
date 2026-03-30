const express = require("express");
const app = express();

let lastUID = null;

// ESP32 sends UID here
app.get("/uid", (req, res) => {
    const id = req.query.id;
    if (id) {
        lastUID = id;
        console.log("RFID UID received:", id);
    }
    res.send("UID received");
});

// Python polls this endpoint every second
app.get("/get-uid", (req, res) => {
    res.json({ uid: lastUID });
    lastUID = null;
});

app.get("/", (req, res) => {
    res.send("RFID Attendance Server Running");
});

// Listen on all interfaces so ESP32 hotspot can reach it
app.listen(3000, "0.0.0.0", () => {
    console.log("Server running on port 3000");
});