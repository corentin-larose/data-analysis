#!/bin/bash
# Script de nettoyage complet de Docker avec confirmation pour chaque étape
# Source des commandes : https://www.calazan.com/docker-cleanup-commands/

set -e

echo "===== Docker Cleanup Script ====="
echo ""

# Fonction pour demander confirmation
confirm() {
    while true; do
        read -rp "$1 [y/N] " yn
        case $yn in
            [Yy]*) return 0 ;;
            [Nn]*|"") return 1 ;;
            *) echo "Merci de répondre par y (oui) ou n (non)." ;;
        esac
    done
}

# 1️⃣ Kill all running containers
if [ "$(docker ps -q)" ]; then
    if confirm "Voulez-vous tuer tous les conteneurs en cours d'exécution ?"; then
        echo "Killing running containers..."
        docker kill $(docker ps -q)
    else
        echo "Skipped killing running containers."
    fi
else
    echo "Aucun conteneur en cours d'exécution."
fi

# 2️⃣ Delete all stopped containers
if [ "$(docker ps -a -q)" ]; then
    if confirm "Voulez-vous supprimer tous les conteneurs arrêtés ?"; then
        echo "Removing stopped containers..."
        docker rm $(docker ps -a -q)
    else
        echo "Skipped removing stopped containers."
    fi
else
    echo "Aucun conteneur arrêté à supprimer."
fi

# 3️⃣ Delete all dangling images
if [ "$(docker images -q -f dangling=true)" ]; then
    if confirm "Voulez-vous supprimer toutes les images <none> (dangling) ?"; then
        echo "Removing dangling images..."
        docker rmi $(docker images -q -f dangling=true)
    else
        echo "Skipped removing dangling images."
    fi
else
    echo "Aucune image dangling à supprimer."
fi

# 4️⃣ Delete all images
if [ "$(docker images -q)" ]; then
    if confirm "Voulez-vous supprimer TOUTES les images Docker ?"; then
        echo "Removing all images..."
        docker rmi -f $(docker images -q)
    else
        echo "Skipped removing all images."
    fi
else
    echo "Aucune image Docker à supprimer."
fi

# 5️⃣ Full system prune (including volumes)
if confirm "Voulez-vous faire un 'docker system prune -a --volumes' ?"; then
    echo "Pruning system..."
    docker system prune -a --volumes
else
    echo "Skipped system prune."
fi

echo ""
echo "===== Docker Cleanup terminé ====="
