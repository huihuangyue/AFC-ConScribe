 docker run -d -p 4444:4444 -p 7777:7900 --shm-size="2g" --name browser_agent_target selenium/standalone-chrome:latest
 docker-compose up --build