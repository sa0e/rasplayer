import csv
import os
from multiprocessing import Process
import random

from playsound import playsound
from os import listdir
from os.path import isfile, join, exists


files_root = '/home/pi/Music/'

print(f'\n\nRasplayer RFID\n')

def playRandom(folder_path, count):
	print(f"Count {count}\n")
	
	file_list = [f for f in listdir(folder_path) if isfile(join(folder_path, f))]
	file_paths = random.sample(file_list, count)
	print(f"paths {file_paths}\n")
	
	while count > 0:	
		count -= 1
		file_path = "".join([folder_path, file_paths[count]])
		print(f"Playing {file_path}\n")
		playsound(file_path)
		

with open('db.csv', newline='') as csv_file:
	csv_reader = csv.DictReader(csv_file)
	line_count = 0
	errorCount = 0
	dbSongs = {}
	dbFlags = {}
	
	for row in csv_reader:
		line_count += 1
		dbSongs[row["id"]] = row["target"];
		dbFlags[row["id"]] = row["flags"];
		print(f'\t{row["id"]} linked to {row["target"]}')
		
		test_path = "".join([files_root, row["target"]])
			
		if not os.path.exists(test_path):
			print(f'\tERROR: Target Invalid!\n\n')
			errorCount += 1
		
	print(f'\nProcessed {line_count} entries.')
	
	if errorCount > 0:
		print(f'\nErrors Encountered!  Check log.\n\n')
	else:
		print(f'\nNo errors.\n\n')
		
	while True:
		scanned_card = input("\nWaiting for card: ")
		
		# If we don't have the card registered, show error
		if scanned_card not in dbSongs:
			print(f'\n\nUnregistered ID: {scanned_card}')
			continue
		
		# If we've already got a background process, kill it
		if 'p' in locals() and p.is_alive():
			print('\nStopping')
			p.terminate()
			
		relativePath = dbSongs[scanned_card];
		flags = dbFlags[scanned_card];
		
		# Flags
		# cmd - Command to be executed, not a path (remove?)
		# rand	- Random file from directory
		# stop	- Stop playback
		# 3shot	- Play 3 random tracks from path
		
		# Random play
		if 'rand' in flags:
			folder_path = "".join([files_root, relativePath])
			
			p = Process(target=playRandom, args=(folder_path, 1, ))
			p.start()
			
		elif '3shot' in flags:
			folder_path = "".join([files_root, relativePath])
			
			p = Process(target=playRandom, args=(folder_path, 3, ))
			p.start()
			
		elif 'stop' in flags:
			continue;

		# Standard play
		else:
			file_path = "".join([files_root, relativePath])
			
			print(f'\nPlaying: {file_path}')
			p = Process(target=playsound, args=(file_path,))
			p.start();



	
