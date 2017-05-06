import tensorflow as tf
from avb.decoders import get_reconstr_err, get_decoder_mean
from avb.utils import *
from tqdm import tqdm

def train(encoder, decoder, adversary, x_real, config):
    is_ac = config['is_ac']
    output_size = config['output_size']
    c_dim = config['c_dim']
    batch_size = config['batch_size']
    cond_dist = config['cond_dist']
    z_dim = config['z_dim']
    z_dist = config['z_dist']
    learning_rate = config['learning_rate']
    learning_rate_adversary = config['learning_rate_adversary']

    factor = 1./(output_size * output_size * c_dim)

    # TODO: support uniform
    if config['z_dist'] != 'gauss':
        raise NotImplementedError

    z_sampled = tf.random_normal([batch_size, z_dim])

    if not is_ac:
        z_real = encoder(x_real, is_training=True)
        Td = adversary(x_real, z_real, is_training=True)
        Ti = adversary(x_real, z_sampled, is_training=True)
        zlogprob = get_zlogprob(z_real, z_dist)
        logr = zlogprob
    else:
        z_real, z_mean, z_var = encoder(x_real, is_training=True)
        z_std = tf.sqrt(z_var + 1e-4)
        z_norm = (z_real - z_mean)/z_std
        Td = adversary(x_real, z_norm, is_training=True)
        Ti = adversary(x_real, z_sampled, is_training=True)
        zlogprob = get_zlogprob(z_real, z_dist)
        logr = -0.5 * tf.reduce_sum(znorm*znorm + tf.log(z_var) + np.log(2*np.pi), [1])

    decoder_out = decoder(z_real, is_training=True)
    x_fake = get_decoder_mean(decoder(z_sampled, is_training=False))

    # Primal loss
    reconst_err = get_reconstr_err(decoder_out, x_real, cond_dist=cond_dist)
    KL = Td + zlogprob
    loss_primal = factor*tf.reduce_mean((reconst_err + Td - zlogprob))

    # Dual loss
    d_loss_d = tf.reduce_mean(
       tf.nn.sigmoid_cross_entropy_with_logits(logits=Td - logr, labels=tf.ones_like(Td))
    )
    d_loss_i = tf.reduce_mean(
        tf.nn.sigmoid_cross_entropy_with_logits(logits=Ti - zlogprob, labels=tf.zeros_like(Ti))
    )

    loss_dual = d_loss_i + d_loss_d

    # Variables
    encoder_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='encoder')
    decoder_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='decoder')
    adversary_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='adversary')

    # Train step
    primal_optimizer = tf.train.AdamOptimizer(learning_rate, use_locking=True, beta1=0.5)
    adversary_optimizer = tf.train.AdamOptimizer(learning_rate_adversary, use_locking=True, beta1=0.5)

    primal_grads = primal_optimizer.compute_gradients(loss_primal, var_list=encoder_vars + decoder_vars)
    adversary_grads = adversary_optimizer.compute_gradients(loss_dual, var_list=adversary_vars)

    primal_grads = [(grad, var) for grad, var in primal_grads if grad is not None]
    adversary_grads = [(grad, var) for grad, var in adversary_grads if grad is not None]

    allgrads = [grad for grad, var in primal_grads + adversary_grads]
    with tf.control_dependencies(allgrads):
        primal_train_step = primal_optimizer.apply_gradients(primal_grads)
        adversary_train_step = adversary_optimizer.apply_gradients(adversary_grads)

    train_op = tf.group(primal_train_step, adversary_train_step)

    # Global step
    global_step = tf.Variable(0, trainable=False)
    global_step_op = global_step.assign_add(1)

    # Test samples
    z_test_np = np.random.randn(batch_size, z_dim)

    # Supervisor
    sv = tf.train.Supervisor(
        logdir=config['log_dir'], global_step=global_step,
        summary_op=None, save_summaries_secs=15,
    )

    # Session
    with sv.managed_session() as sess:
        # Show real data
        samples = sess.run(x_real)
        samples = 0.5*(samples+1.)
        save_images(samples[:64], [8, 8], config['sample_dir'], 'real.png')

        progress = tqdm(range(config['nsteps']))
        for batch_idx in progress:
            if sv.should_stop():
               break

            niter = sess.run(global_step)

            # Train
            sess.run(train_op)

            loss_primal_out, loss_dual_out = sess.run([loss_primal, loss_dual])

            progress.set_description('Loss_g: %4.4f, Loss_d: %4.4f'
                % (loss_primal_out, loss_dual_out))

            sess.run(global_step_op)
            if np.mod(niter, config['ntest']) == 0:
                # Test
                samples = sess.run(x_fake, feed_dict={z_sampled: z_test_np})
                samples = 0.5*(samples+1.)

                save_images(samples[:64], [8, 8], os.path.join(config['sample_dir'], 'samples'),
                            'train_{:06d}.png'.format(niter)
                )


def get_zlogprob(z, z_dist):
    if z_dist == "gauss":
        logprob = -0.5 * tf.reduce_sum(z*z  + np.log(2*np.pi), [1])
    elif z_dist == "uniform":
        logprob = 0.
    else:
        raise ValueError("Invalid parameter value for `z_dist`.")
    return logprob
