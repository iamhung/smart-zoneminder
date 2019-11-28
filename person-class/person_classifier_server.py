"""
Classify persons using tensorflow-gpu served by zerorpc

Should be called from a zerorpc client with ZoneMinder
alarm image metadata from zm-s3-upload.js.

This program should be run in the 'od' virtual python environment, i.e.,
$ /home/lindo/.virtualenvs/od/bin/python ./person_classifier_server.py

This is part of the smart-zoneminder project.
See https://github.com/goruck/smart-zoneminder

Copyright (c) 2019 Lindo St. Angel
"""

import numpy as np
import tensorflow as tf
import cv2
import json
import zerorpc
import logging
import gevent
import signal
from keras.applications.vgg16 import preprocess_input as VGG16_preprocess_input
from keras.applications.inception_resnet_v2 import preprocess_input as inception_preprocess_input

logging.basicConfig(level=logging.ERROR)

# Get configuration.
with open('./config.json') as fp:
    config = json.load(fp)['personClassifierServer']

# Tensorflow object detection file system paths.
PATH_TO_MODEL = config['modelPath']

# Minimum score for valid TF person detection. 
MIN_PROBA = config['minProba']

# Heartbeat interval for zerorpc client in ms.
# This must match the zerorpc client config. 
ZRPC_HEARTBEAT = config['zerorpcHeartBeat']

# IPC (or TCP) socket for zerorpc.
# This must match the zerorpc client config.
ZRPC_PIPE = config['zerorpcPipe']

# Get tf label map. 
LABEL_MAP = config['labelMap']

# Get model architecture type.
MODEL_ARCH = config['modelArch']
assert MODEL_ARCH in {'VGG16','InceptionResNetV2'},'Must be "VGG16" or "InceptionResNetV2"'
if MODEL_ARCH == 'VGG16':
    input_size = (224, 224)
    preprocessor = VGG16_preprocess_input
    model_input_name = 'vgg16_input:0'
    model_output_name = 'dense_3/Softmax:0'
else:
    input_size = (299, 299)
    preprocessor = inception_preprocess_input
    model_input_name = 'inception_resnet_v2_input:0'
    model_output_name = 'dense_3/Softmax:0'

# Only grow the gpu memory tf usage as required.
# See https://www.tensorflow.org/guide/using_gpu#allowing-gpu-memory-growth
tf_config = tf.ConfigProto()
tf_config.gpu_options.allow_growth=True

# Load frozen tf model into memory.
detection_graph = tf.Graph()
with detection_graph.as_default():
    od_graph_def = tf.GraphDef()
    with tf.gfile.GFile(PATH_TO_MODEL, 'rb') as fid:
        serialized_graph = fid.read()
        od_graph_def.ParseFromString(serialized_graph)
        tf.import_graph_def(od_graph_def, name='')

# zerorpc class.
class DetectRPC(object):
    def __init__(self):
        logging.debug('Starting tf sess for person classification.')
        self.sess = tf.Session(config=tf_config, graph=detection_graph)

    def close_sess(self):
        logging.debug('Closing tf sess for person classification.')
        self.sess.close()

    def detect_faces(self, test_image_paths):
        # List that will hold all images with any person classifications. 
        objects_classified_persons = []
        for obj in test_image_paths:
            logging.debug('**********Classify person for {}'.format(obj['image']))
            for label in obj['labels']:
                # If the object detected is a person then try to identify face. 
                if label['name'] == 'person':
                    # Read image from disk. 
                    img = cv2.imread(obj['image'])
                    if img is None:
                        # Bad image was read.
                        logging.error('Bad image was read.')
                        label['face'] = None
                        continue

                    # First bound the roi using the coord info passed in.
                    # The roi is area around person(s) detected in image.
                    # (x1, y1) are the top left roi coordinates.
                    # (x2, y2) are the bottom right roi coordinates.
                    y2 = int(label['box']['ymin'])
                    x1 = int(label['box']['xmin'])
                    y1 = int(label['box']['ymax'])
                    x2 = int(label['box']['xmax'])
                    roi = img[y2:y1, x1:x2]
                    #cv2.imwrite('./roi.jpg', roi)
                    if roi.size == 0:
                        # Bad object roi...move on to next image.
                        logging.error('Bad object roi.')
                        label['face'] = None
                        continue

                    # Format image to what the model expects for input.
                    # Resize.
                    roi = cv2.resize(roi, dsize=input_size, interpolation=cv2.INTER_AREA)
                    # Expand dimensions.
                    roi = np.expand_dims(roi, axis=0)
                    # Preprocess.
                    roi = preprocessor(roi.astype('float32'))

                    # Define input tensor.
                    #input_tensor = detection_graph.get_tensor_by_name('vgg16_input:0')
                    input_tensor = detection_graph.get_tensor_by_name(model_input_name)

                    # Define output tensor.
                    output_tensor = detection_graph.get_tensor_by_name(model_output_name)

                    # Actual predictions per class.
                    predictions = self.sess.run(output_tensor, {input_tensor: roi})[0]

                    # Find most likely prediction.
                    j = np.argmax(predictions)
                    proba = predictions[j]
                    person = LABEL_MAP[j]
                    logging.debug('person classifier proba {} name {}'.format(proba, person))
                    if proba >= MIN_PROBA:
                        name = person
                        logging.debug('person classifier says this is {}'.format(name))
                    else:
                        name = None # prob too low to recog face
                        logging.debug('person classifier cannot recognize person')

                    # Add face name to label metadata.
                    label['face'] = name
                    # Add face confidence to label metadata.
                    # (First convert NumPy value to native Python type for json serialization.)
                    label['faceProba'] = proba.item()

                    # Add processed image to output list. 
            objects_classified_persons.append(obj)

        # Convert json to string and return data. 
        return(json.dumps(objects_classified_persons))

# Create zerorpc object. 
zerorpc_obj = DetectRPC()
# Create and bind zerorpc server. 
s = zerorpc.Server(zerorpc_obj, heartbeat=ZRPC_HEARTBEAT)
s.bind(ZRPC_PIPE)
# Register graceful ways to stop server. 
gevent.signal(signal.SIGINT, s.stop) # Ctrl-C
gevent.signal(signal.SIGTERM, s.stop) # termination
# Start server.
# This will block until a gevent signal is caught
s.run()
# After server is stopped then close the tf session. 
zerorpc_obj.close_sess()