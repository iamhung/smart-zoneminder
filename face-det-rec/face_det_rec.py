#
# Face detection and recognition using dlib and the face_recognition api.
# 
# References:
# See https://github.com/ageitgey/face_recognition.
# See https://www.pyimagesearch.com/2018/06/18/face-recognition-with-opencv-python-and-deep-learning/.
#
# Part of the smart-zoneminder project:
# See https://github.com/goruck/smart-zoneminder.
#
# Copyright (c) 2018, 2019 Lindo St. Angel.
#

import face_recognition
import argparse
import pickle
import cv2
import json
from sys import argv
from sys import exit
from sys import stdout

# File path to log diagnostics. 
LOG_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/face_det_rec.log'

# Path to known face encodings in python pickle serialization format.
# The pickle file needs to be generated by the 'encode_faces.py' program first.
KNOWN_FACE_ENCODINGS_PATH = '/home/lindo/develop/smart-zoneminder/face-det-rec/encodings.pickle'

# Face comparision tolerance.
# A lower value causes stricter compares which may reduce false positives.
# See https://github.com/ageitgey/face_recognition/wiki/Face-Recognition-Accuracy-Problems.
COMPARE_FACES_TOLERANCE = 0.57

# Factor to scale image when looking for faces.
# May increase the probability of finding a face in the image. 
# Use caution setting the value > 1 since you may run out of memory.
# See https://github.com/ageitgey/face_recognition/wiki/Face-Recognition-Accuracy-Problems.
NUMBER_OF_TIMES_TO_UPSAMPLE = 1

# Face detection model to use. Can be either 'cnn' or 'hog'.
FACE_DET_MODEL = 'cnn'

# How many times to re-sample when calculating face encoding.
NUM_JITTERS = 100

# Threshold to declare a valid face.
# This is the percentage of all embeddings for a face name. 
NAME_THRESHOLD = 0.20

# Images with Variance of Laplacian less than this are declared blurry. 
FOCUS_MEASURE_THRESHOLD = 1000

# Get image paths from command line.
if len(argv) == 1:
    exit('No test image file paths were supplied!')

# Construct list from images given on command line. 
objects_detected = argv[1:]

# Load the known faces and embeddings.
with open(KNOWN_FACE_ENCODINGS_PATH, 'rb') as fp:
    data = pickle.load(fp)

# Calculate number of embeddings for each face name.
name_count = {n: 0 for n in data['names']}
for name in data['names']:
	name_count[name] += 1

def variance_of_laplacian(image):
	# compute the Laplacian of the image and then return the focus
	# measure, which is simply the variance of the Laplacian
	return cv2.Laplacian(image, cv2.CV_64F).var()

def log(msg):
	with open(LOG_PATH, 'a') as f:
		print(msg, file=f)

# List that will hold all images with any face detection information. 
objects_detected_faces = []

# Loop over the images given in the command line.  
for obj in objects_detected:
	json_obj = json.loads(obj)
	img = cv2.imread(json_obj['image'])
	height, width, channels = img.shape

	for label in json_obj['labels']:
		# If the object detected is a person then try to identify face. 
		if label['name'] == 'person':
			# First bound the roi using the coord info passed in.
			# The roi is area around person(s) detected in image.
			# (x1, y1) are the top left roi coordinates.
			# (x2, y2) are the bottom right roi coordinates.
			y2 = int(label['box']['ymin'])
			x1 = int(label['box']['xmin'])
			y1 = int(label['box']['ymax'])
			x2 = int(label['box']['xmax'])
			roi = img[y2:y1, x1:x2, :]

			# Bad object roi...move on to next image.
			if roi.size == 0:
				label['face'] = None
				continue

			# Detect the (x, y)-coordinates of the bounding boxes corresponding
			# to each face in the input image.
			rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
			box = face_recognition.face_locations(rgb, NUMBER_OF_TIMES_TO_UPSAMPLE,
				FACE_DET_MODEL)

			# No face detected...move on to next image.
			if not box:
				label['face'] = None
				continue

			# Carve out face roi from object roi. 
			face_top, face_right, face_bottom, face_left = box[0]
			face_roi = roi[face_top:face_bottom, face_left:face_right, :]

			# Compute the focus measure of the face
			# using the Variance of Laplacian method.
			# See https://www.pyimagesearch.com/2015/09/07/blur-detection-with-opencv/
			gray = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
			fm = variance_of_laplacian(gray)

			# If fm below a threshold then face probably isn't clear enough
			# for face recognition to work, so skip it. 
			if fm < FOCUS_MEASURE_THRESHOLD:
				name = None
			else:
				# Return the 128-dimension face encoding for each face in the image.
				# TODO - figure out why encodings are slightly different in
				# face_det_rec.py for same image
				encodings = face_recognition.face_encodings(rgb, box, NUM_JITTERS)

				# initialize the list of names for each face detected
				names = []

				# loop over the facial embeddings
				for encoding in encodings:
					# attempt to match each face in the input image to our known encodings
					matches = face_recognition.compare_faces(data['encodings'],
						encoding, COMPARE_FACES_TOLERANCE)

					# Assume face is unknown to start with. 
					name = 'Unknown'

					# check to see if we have found a match
					if True in matches:
						# find the indexes of all matched faces then initialize a
						# dictionary to count the total number of times each face
						# was matched
						matchedIdxs = [i for (i, b) in enumerate(matches) if b]

						# init all name counts to 0
						counts = {n: 0 for n in data['names']}

						# loop over the matched indexes and maintain a count for
						# each recognized face
						for i in matchedIdxs:
							name = data['names'][i]
							counts[name] = counts.get(name, 0) + 1

						# Find face name with the max count value.
						max_value = max(counts.values())
						max_name = max(counts, key=counts.get)

						# Compare each recognized face against the max face name.
						# The max face name count must be greater than a certain value for
						# it to be valid. This value is set at a percentage of the number of
						# embeddings for that face name. 
						name_thresholds = [max_value > value + NAME_THRESHOLD * name_count[max_name]
							for name, value in counts.items() if name != max_name]

						# If max face name passes against all other faces then declare it valid.
						if all(name_thresholds):
							name = max_name
						else:
							name = None

			# Add face name to label metadata.
			label['face'] = name

	# Add processed image to output list. 
	objects_detected_faces.append(json_obj)

# Convert json to string and return data. 
print(json.dumps(objects_detected_faces))
stdout.flush()